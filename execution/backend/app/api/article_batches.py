from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.clock import seconds_since, utcnow
from app.models.article import Article, ArticleBatch, ArticleImage
from app.models.job import JobRun
from app.models.site import Site
from app.models.user import User
from app.tasks import generate_topics, regenerate_article, retry_article, run_batch

router = APIRouter(prefix="/api", tags=["articles"])

EDITABLE_STATUSES = {"topics_pending", "topics_review", "failed", "paused"}


class BatchIn(BaseModel):
    site_id: int
    count: int = Field(ge=1, le=50)


class ArticleOut(BaseModel):
    id: int
    topic: str
    title: str
    status: str
    remote_url: str
    error_text: str
    regenerating: bool


class BatchOut(BaseModel):
    id: int
    site_id: int
    site_name: str
    site_domain: str
    requested_count: int
    status: str
    error_text: str
    created_at: datetime
    # Вычисляется на чтении, в БД не хранится: queued | working | pausing |
    # stuck для партии в running, иначе None. См. batch_runtime_state ниже.
    runtime_state: str | None = None
    run_requested_at: datetime | None = None
    articles: list[ArticleOut] = []


def _to_out(db: Session, batch: ArticleBatch) -> BatchOut:
    site = db.get(Site, batch.site_id)
    return BatchOut(
        id=batch.id, site_id=batch.site_id,
        site_name=site.name if site else "—",
        site_domain=site.domain if site else "—",
        requested_count=batch.requested_count, status=batch.status,
        error_text=batch.error_text, created_at=batch.created_at,
        runtime_state=batch_runtime_state(db, batch),
        run_requested_at=batch.run_requested_at,
        articles=[ArticleOut(id=a.id, topic=a.topic, title=a.title, status=a.status,
                             remote_url=a.remote_url, error_text=a.error_text,
                             regenerating=a.regenerating)
                  for a in batch.articles],
    )


def _get_or_404(db: Session, batch_id: int) -> ArticleBatch:
    batch = db.get(ArticleBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "партия не найдена")
    return batch


@router.get("/article-batches", response_model=list[BatchOut])
def list_batches(db: Session = Depends(get_db),
                 _user: User = Depends(get_current_user)):
    batches = db.scalars(select(ArticleBatch).order_by(ArticleBatch.id.desc())).all()
    return [_to_out(db, b) for b in batches]


@router.post("/article-batches", response_model=BatchOut)
def create_batch(payload: BatchIn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    site = db.get(Site, payload.site_id)
    if site is None:
        raise HTTPException(404, "сайт не найден")
    # Находка №4 ревью Task 18: GET /api/sites (Task 9/11) уже фильтрует
    # is_active — обычный UI-пикер не предложит неактивный сайт. Но этот
    # эндпоинт принимает site_id напрямую, и прямой POST в обход пикера
    # (например, из старой открытой вкладки браузера, где сайт был активен
    # на момент открытия формы) создал бы партию для сайта, с которым,
    # возможно, уже не работают. Решение: отклонять явно, а не молчать —
    # деактивация сайта осмысленно должна останавливать создание новых работ
    # по нему, симметрично тому, как она уже останавливает его показ в
    # выпадающем списке.
    if not site.is_active:
        raise HTTPException(400, "сайт деактивирован — создание партий недоступно")
    batch = ArticleBatch(site_id=payload.site_id, requested_count=payload.count,
                         created_by_id=user.id)
    db.add(batch)
    db.commit()
    generate_topics.delay(batch.id)
    return _to_out(db, batch)


@router.get("/article-batches/{batch_id}", response_model=BatchOut)
def read_batch(batch_id: int, db: Session = Depends(get_db),
               _user: User = Depends(get_current_user)):
    return _to_out(db, _get_or_404(db, batch_id))


# Бюджет времени на одну статью в партии. Складывается из худшего случая
# текстовых вызовов (≈366 с на один вызов: 120 с таймаута × 3 попытки плюс
# паузы backoff, см. app/ai/text.py; вызовов на статью несколько — тело,
# промпт на каждую картинку, промпт обложки, — но они не суммируются в этот
# бюджет впритык, а покрываются тем же запасом, что и публикация) плюс
# худший случай пачки картинок, которые генерируются параллельно
# (`ThreadPoolExecutor`, см. app/ai/images.py) — тоже ≈365 с на пачку, а не
# на картинку, — плюс запас на публикацию. Это граница «задача зависла», а
# не ожидаемая длительность: типовая статья укладывается в разы быстрее.
# Раньше здесь стояло 420 — ровно столько же, сколько был таймаут ОДНОЙ
# попытки генерации ОДНОЙ картинки (TIMEOUT=420 в старой версии
# app/ai/images.py, до трёх попыток с retry — то есть до ≈1275 с на одну
# картинку). Ревью Task 8 показало и посчитало это несоответствие; заодно
# TIMEOUT там снижен до 180, а max_retries — до 2 (см. Task 8, Step 8).
ARTICLE_TIME_BUDGET_SECONDS = 900
# Запас на подготовку: открытие клиента сайта, чтение эталона, разбор списка.
BATCH_OVERHEAD_SECONDS = 300
# Потолок на случай, если ограничение числа статей в партии когда-нибудь
# ослабят: без него опечатка в количестве поставила бы задачу на сутки.
# ВНИМАНИЕ (открытый вопрос ревью Task 8): при текущем максимуме партии в 50
# статей (см. test_count_is_bounded) этот потолок связывает бюджет:
# soft = min(300 + 900×50, 21600) = 21600, то есть на партию из 50 статей
# приходится ≈432 с на статью (21600/50), а резать бюджет потолок начинает
# уже примерно с 24 статей.
#
# Это осознанное решение, а не недосмотр. ARTICLE_TIME_BUDGET_SECONDS = 900 —
# граница «статья зависла», а не ожидаемая длительность: типовая статья
# укладывается в 2–4 минуты, то есть партия из 50 штук проходит за 2–3 часа
# и до потолка не доходит. Упереться в него можно только если зависла не одна
# статья, а значительная часть партии.
#
# Ключевое: упереться в потолок не разрушительно. Обработчик
# SoftTimeLimitExceeded (Task 17) помечает партию как failed — не как running,
# — с указанием, сколько статей успело опубликоваться; повторный запуск
# разрешён (эндпоинт run отклоняет только status="running"), а run_batch_sync
# пропускает уже опубликованные статьи. То есть партия продолжается с места
# остановки, а не начинается заново и не оплачивается повторно.
#
# Поднимать потолок до ~12.6 часа (300 + 900×50) было бы хуже: воркеров всего
# два, и одна задача, держащая слот полсуток, останавливает работу остальных
# надолго. Оборвать и продолжить дешевле, чем ждать.
BATCH_TIME_LIMIT_CAP_SECONDS = 6 * 60 * 60
# Разрыв между мягким и жёстким лимитом: столько есть у обработчика
# SoftTimeLimitExceeded в tasks.py, чтобы записать отказ в журнал и закрыть
# сессию БД до принудительного завершения процесса.
TIME_LIMIT_GAP_SECONDS = 180


def _batch_time_limits(article_count: int) -> tuple[int, int]:
    soft = min(BATCH_OVERHEAD_SECONDS + ARTICLE_TIME_BUDGET_SECONDS * article_count,
               BATCH_TIME_LIMIT_CAP_SECONDS)
    return soft, soft + TIME_LIMIT_GAP_SECONDS


# Сколько партия может простоять в очереди, прежде чем отсутствие JobRun станет
# подозрительным. Воркеров два (--concurrency=2), партия идёт часами, так что
# реальное ожидание бывает долгим — но JobRun создаётся в первую же секунду
# РЕАЛЬНОГО старта задачи, и десяти минут с запасом хватает, чтобы отличить
# «ждёт слот» от «постановка в брокер не удалась и задачи нет вовсе».
# Влияет только на подпись в интерфейсе и на доступность кнопки «Дособрать»,
# не на саму сборку.
QUEUE_GRACE_SECONDS = 600


def _latest_run_job(db: Session, batch_id: int) -> JobRun | None:
    """Последняя джоба сборки этой партии. Партия ищется по params_json, а не по
    отдельной колонке-связке: batch_id там лежит с Task 17, и вторая ссылка на
    то же самое разошлась бы с первой при первом же повторном запуске.
    Индексация JSON одинаково работает на JSONB (прод) и JSON (SQLite в
    тестах) — проверено на обоих контурах."""
    return db.scalars(
        select(JobRun)
        .where(JobRun.kind == "run_batch",
               JobRun.params_json["batch_id"].as_integer() == batch_id)
        .order_by(JobRun.id.desc())
        .limit(1)
    ).first()


def batch_runtime_state(db: Session, batch: ArticleBatch) -> str | None:
    """queued | working | stuck (см. _task_state) либо pausing — задача жива, но
    менеджер попросил остановиться: она доделает текущую статью и сама
    переведёт партию в paused. Зависшую партию пауза не спасает — там stuck."""
    state = _task_state(db, batch)
    if state in ("queued", "working") and batch.pause_requested_at is not None:
        return "pausing"
    return state


def _task_state(db: Session, batch: ArticleBatch) -> str | None:
    """Идёт ли сборка на самом деле: queued | working | stuck, либо None, если
    партия не в running и вопрос не стоит.

    Зачем это вообще нужно: статус партии в БД — обещание, и исправить его
    может только сама задача. Если задача умерла жёстко (SIGKILL по лимиту,
    OOM, рестарт контейнера при деплое), обещание остаётся навсегда. Партия 25
    простояла так девять дней, и по панели это было неотличимо от работы
    (см. directions/2026-09-12-batch-resilience-design.md §6.1).

    Сознательно НЕ спрашиваем Celery через AsyncResult: у Redis-бэкенда
    результат живёт сутки, после чего PENDING приходит и для мёртвой задачи, и
    для стоящей в очереди, — то есть именно в тех случаях, которые надо
    различить, ответ бесполезен. Журнал JobRun хранится вечно и даёт больше.
    """
    if batch.status != "running":
        return None

    job = _latest_run_job(db, batch.id)
    # Джоба, начавшаяся ДО текущего запроса на запуск, относится к предыдущей
    # попытке и о нынешней ничего не говорит. Без этой отсечки сразу после
    # перезапуска зависшей партии срабатывало правило «джоба завершена, а партия
    # running» — и партия объявлялась зависшей снова, хотя её только что
    # запустили. В проде окно до реального старта задачи — минуты (воркеров
    # два), и всё это время интерфейс предлагал бы «Дособрать партию» ещё раз,
    # то есть звал бы поставить вторую задачу на ту же партию и оплатить её
    # дважды. Найдено прогоном на живом стенде, а не тестами — тесты пришли
    # следом (test_runtime_state_after_restart_is_queued_not_stuck).
    # Сравниваем через seconds_since: «прошло больше времени» == «началось
    # раньше», и это единственный способ сравнить два момента, не напоровшись
    # на naive/aware (см. app/clock.py).
    if (job is not None and batch.run_requested_at is not None
            and seconds_since(job.started_at) > seconds_since(batch.run_requested_at)):
        job = None

    if job is None:
        # Задачи ещё (или уже) нет. run_requested_at == None — партия в running
        # без записи о запуске: так не бывает в штатном потоке (run() пишет его
        # одним коммитом со статусом), значит состояние испорчено и это stuck.
        if batch.run_requested_at is None:
            return "stuck"
        waiting = seconds_since(batch.run_requested_at)
        return "queued" if waiting < QUEUE_GRACE_SECONDS else "stuck"

    if job.status != "running":
        # Джоба закрыта, а партия всё ещё running — противоречие: закрывает
        # джобу тот же код, который дальше доводит партию до done/failed,
        # поэтому сюда можно попасть только если он до этого не дошёл.
        return "stuck"

    # Жёсткий лимит берём из той же функции, что выставляла его при постановке,
    # и по тому же числу статей — иначе появится второй источник правды о том,
    # сколько партии отведено.
    _, hard = _batch_time_limits(len(batch.articles))
    return "working" if seconds_since(job.started_at) < hard else "stuck"


# Находка №4 ревью Task 17 (полный расчёт — в app/celery_app.py, раздел
# «⚠️ Находка №4»). ARTICLE_TIME_BUDGET_SECONDS=900 выше — не точный худший
# случай одной статьи, а генерозный средний слот ВНУТРИ СУММЫ на партию:
# он безопасен для _batch_time_limits, потому что переплата на одних статьях
# компенсируется недоплатой на других, а типовая статья укладывается в разы
# быстрее. Для retry_article такой компенсации нет — это ВСЕГДА ровно одна
# статья, и весь вес её реального худшего случая ложится на лимит без
# усреднения. Реальный худший случай: 1462 + 366×N секунд, где
# N = site.reference_images (366 — тело статьи, 366×N — N ПОСЛЕДОВАТЕЛЬНЫХ
# текстовых промптов контентных картинок, 365 — параллельная пачка самих
# картинок, 366 — промпт обложки, 365 — обложка). Использовать здесь
# ARTICLE_TIME_BUDGET_SECONDS=900 было бы недостаточно уже при N=1
# (1462+366=1828 с) — отдельные константы ниже, не переиспользование.
_RETRY_FIXED_SECONDS = 1462     # тело + пачка картинок + промпт обложки + обложка
_RETRY_PER_IMAGE_SECONDS = 366  # один последовательный текстовый промпт картинки


def _retry_time_limits(reference_images: int) -> tuple[int, int]:
    soft = _RETRY_FIXED_SECONDS + _RETRY_PER_IMAGE_SECONDS * reference_images
    return soft, soft + TIME_LIMIT_GAP_SECONDS


# Перегенерация не создаёт страницу заново — бюджет считается только по
# реально выбранным частям, а не всегда «по максимуму»: текст — один
# последовательный вызов генерации тела (_REGEN_TEXT_SECONDS); картинки — N
# последовательных текстовых промптов иллюстраций (_RETRY_PER_IMAGE_SECONDS
# каждый) плюс одна параллельная пачка генерации самих картинок
# (_REGEN_IMAGE_BATCH_SECONDS); обложка — промпт обложки плюс сама картинка
# (_REGEN_COVER_SECONDS). _REGEN_OVERHEAD_SECONDS — общий запас на загрузку
# файлов и update_page_text, один раз независимо от набора частей.
_REGEN_OVERHEAD_SECONDS = 300
_REGEN_IMAGE_BATCH_SECONDS = 365
_REGEN_TEXT_SECONDS = 366          # один вызов генерации тела статьи
_REGEN_COVER_SECONDS = 366 + 365   # промпт обложки + сама картинка


def _regen_time_limits(*, text: bool, image_count: int, cover: bool) -> tuple[int, int]:
    soft = _REGEN_OVERHEAD_SECONDS
    if text:
        soft += _REGEN_TEXT_SECONDS
    if image_count:
        soft += _RETRY_PER_IMAGE_SECONDS * image_count + _REGEN_IMAGE_BATCH_SECONDS
    if cover:
        soft += _REGEN_COVER_SECONDS
    return soft, soft + TIME_LIMIT_GAP_SECONDS


def _reset_stuck_batch(db: Session, batch: ArticleBatch) -> None:
    """Привести в порядок состояние партии, чья задача умерла, не закрыв за
    собой: статьи, застрявшие в «Генерируется», и незакрытую джобу.

    Статьи в `generating` обязательно перевести в `failed`: сборка их всё равно
    пересоберёт (пропускается только `published`), но в статусе `generating`
    их собственная кнопка повтора недоступна — а она может понадобиться, если
    именно эта статья упадёт снова. Опубликованные не трогаем: страницы на
    сайте существуют, и повторно за них не платят.

    Джобу закрываем, иначе она вечно считается в «Выполняется сейчас» на экране
    журнала и скрывает реальную картину — так три таких записи и накопились
    к 2026-09-12 (джобы 29, 140, 183).
    """
    for article in batch.articles:
        if article.status == "generating":
            article.status = "failed"
            article.error_text = ("задача партии оборвалась на этой статье — "
                                 "она не досчитана")
    job = _latest_run_job(db, batch.id)
    if job is not None and job.status == "running":
        job.status = "failed"
        job.finished_at = utcnow()
        job.log_text = "задача оборвалась, не закрыв журнал — партия перезапущена вручную"
    db.commit()


class TopicsIn(BaseModel):
    topics: list[str]


@router.put("/article-batches/{batch_id}/topics", response_model=BatchOut)
def save_topics(batch_id: int, payload: TopicsIn, db: Session = Depends(get_db),
                _user: User = Depends(get_current_user)):
    batch = _get_or_404(db, batch_id)
    if batch.status not in EDITABLE_STATUSES:
        raise HTTPException(400, "темы уже отправлены в работу — правка невозможна")
    # Находка №1 ревью Task 18: EDITABLE_STATUSES включает "failed", а партия
    # становится "failed" не только когда генерация тем не удалась (тогда у
    # неё гарантированно нет ни одной Article), но и когда run_batch_sync
    # (Task 17) обрывается посреди сборки — по SoftTimeLimitExceeded или по
    # AIConfigError/SecretDecryptionError — и часть статей к этому моменту
    # уже реально status="published" (черновик реально создан на сайте,
    # remote_page_id/remote_url заполнены). Код ниже безусловно удалял бы
    # ВСЕ batch.articles и создавал новые с нуля — то есть стёр бы из своей
    # БД запись об уже реально опубликованных страницах, не тронув сами
    # страницы на сайте. Это ровно то, что Task 14 сознательно защищала
    # (ON DELETE SET NULL, докстринг «партия и её статьи — это журнал того,
    # что было реально опубликовано»).
    #
    # Решение: полный отказ 400, если в партии есть хоть одна опубликованная
    # статья — не частичное удаление с сохранением опубликованных. Причина
    # выбора именно этого варианта, а не «удалить только неопубликованные,
    # добавить новые темы поверх»: если часть статей партии уже опубликована,
    # значит партия реально была запущена и частично прошла — «согласование
    # тем» в этот момент больше не осмысленная операция (темы уже отработаны
    # для опубликованных статей, а для неопубликованных исправление — это
    # retry конкретной статьи, /api/articles/{id}/retry, а не замена списка
    # тем всей партии). Полный отказ с понятным текстом проще для пользователя
    # панели, чем частичная операция, результат которой (что осталось, что
    # исчезло) сложно предсказать по одному отклику.
    if any(a.status == "published" for a in batch.articles):
        raise HTTPException(
            400,
            "в партии уже есть опубликованные статьи — правка тем невозможна, "
            "используйте повтор отдельной статьи")

    # Согласованный список заменяет предложенный целиком: менеджер мог
    # переписать формулировки, а не только вычеркнуть лишнее.
    for article in list(batch.articles):
        db.delete(article)
    db.flush()
    for topic in [t.strip() for t in payload.topics if t.strip()]:
        db.add(Article(batch_id=batch.id, site_id=batch.site_id, topic=topic))
    batch.status = "topics_review"
    db.commit()
    db.refresh(batch)
    return _to_out(db, batch)


@router.post("/article-batches/{batch_id}/run", response_model=BatchOut)
def run(batch_id: int, db: Session = Depends(get_db),
        _user: User = Depends(get_current_user)):
    batch = _get_or_404(db, batch_id)
    if not batch.articles:
        raise HTTPException(400, "в партии нет тем")
    if batch.status == "running":
        # До 2026-09-13 здесь был безусловный отказ, и партию, зависшую в
        # running, нельзя было перезапустить вообще ничем — ни из интерфейса,
        # ни через API. Партия 25 простояла так девять дней и потребовала
        # правки БД руками. Теперь отказ только когда сборка действительно
        # идёт или честно ждёт очереди; зависшую разрешаем перезапустить,
        # предварительно приведя её состояние в порядок.
        state = batch_runtime_state(db, batch)
        if state != "stuck":
            raise HTTPException(400, {
                "queued": "партия ждёт свободный воркер",
                "pausing": "партия останавливается — дождитесь, пока доделается текущая статья",
            }.get(state, "партия уже выполняется"))
        _reset_stuck_batch(db, batch)
    # Находка №2 ревью Task 18: раньше в "running" партию переводила только
    # run_batch_sync (app/tasks.py) — АСИНХРОННО, когда Celery реально начнёт
    # исполнять задачу. Между apply_async(...) ниже и фактическим стартом
    # задачи (обычно доли секунды, но может быть больше при загруженной
    # очереди) batch.status оставался прежним — если run() вызвать повторно
    # в этом окне (двойной клик, повторный запрос из-за таймаута фронта),
    # проверка выше пропускала второй вызов, и в очередь уходили ДВЕ задачи
    # run_batch на одну партию: два воркера одновременно шли по одному и тому
    # же batch.articles и оплачивали LLM/картинки дважды для части статей.
    # Проверено эмпирически (см. test_run_twice_dispatches_once в
    # test_api_batches.py): без строк ниже два подряд идущих run() дают два
    # элемента ("run", ...) в списке диспетчеризаций.
    #
    # Фикс: перевод в "running" происходит здесь же, синхронно, в той же
    # транзакции, что и проверка выше, — до постановки задачи в очередь.
    # Второй вызов после этого коммита увидит status == "running" и получит
    # 400 ещё до apply_async. Строка `batch.status = "running"` в начале
    # run_batch_sync (app/tasks.py) убрана этим же изменением — Celery-задача
    # запускается только через этот эндпоинт, и к моменту её реального
    # старта партия уже находится в "running"; отдельное присваивание там
    # стало мёртвым кодом, а не защитой (см. коммит и обоснование там же).
    #
    # Остаточный риск: это не SELECT ... FOR UPDATE, поэтому теоретическая
    # гонка двух ПОДЛИННО одновременных запросов на разных потоках/процессах
    # (не последовательных HTTP-вызовов, а буквально одновременного чтения
    # старого статуса до commit друг друга) не исключена на 100% под
    # Postgres. Но она сужена с «сколько угодно долго, пока задача не
    # стартует в очереди» до «доли миллисекунды между чтением и записью в
    # рамках одного HTTP-запроса» — а именно такую гонку (двойной клик,
    # повторный запрос) и требовалось закрыть.
    batch.status = "running"
    # Одним коммитом со статусом: пара (running, run_requested_at) должна быть
    # согласованной, иначе batch_runtime_state увидит running без момента
    # запуска и сочтёт партию сломанной.
    batch.run_requested_at = utcnow()
    batch.pause_requested_at = None
    db.commit()
    # Лимит времени вычисляется здесь, а не берётся из глобальной настройки
    # Celery: партия идёт последовательно, и её длительность пропорциональна
    # числу статей. Глухой статический лимит обрывал бы работу на середине —
    # часть статей опубликована, часть нет.
    soft, hard = _batch_time_limits(len(batch.articles))
    run_batch.apply_async(args=[batch.id], soft_time_limit=soft, time_limit=hard)
    return _to_out(db, batch)


@router.post("/article-batches/{batch_id}/pause", response_model=BatchOut)
def pause(batch_id: int, db: Session = Depends(get_db),
          _user: User = Depends(get_current_user)):
    """«Приостановить генерацию». Статью посреди сборки не обрываем — её текст
    и картинки уже оплачены; задача остановится перед следующей. Продолжение —
    тот же run() («Дособрать партию»): опубликованные статьи пропускаются."""
    batch = _get_or_404(db, batch_id)
    if batch.status != "running":
        raise HTTPException(400, "партия сейчас не собирается")
    state = batch_runtime_state(db, batch)
    if state == "pausing":
        raise HTTPException(400, "остановка уже запрошена — доделываем текущую статью")
    if state == "stuck":
        # Задачи нет — ждать некого, останавливаем сразу.
        _reset_stuck_batch(db, batch)
        batch.status = "paused"
        batch.pause_requested_at = None
    else:
        batch.pause_requested_at = utcnow()
    db.commit()
    return _to_out(db, batch)


@router.post("/articles/{article_id}/retry")
def retry(article_id: int, db: Session = Depends(get_db),
          _user: User = Depends(get_current_user)):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(404, "статья не найдена")
    # Находка №2 ревью Task 18 (та же природа гонки, что и у run() выше,
    # применённая к одиночной статье). ArticleBuilder.build() (Task 16,
    # app/articles/builder.py) сама переводит статью в status="generating"
    # первым делом — но делает это ВНУТРИ Celery-задачи, то есть асинхронно
    # относительно момента, когда этот эндпоинт вызвал apply_async(...) и
    # вернул ответ. Окно гонки у retry уже, чем у run() (одна статья, а не
    # партия из N — двойная оплата ограничена стоимостью одной статьи, а не
    # умножается на размер партии), но оно есть: два быстрых клика «повторить»
    # по одной и той же упавшей статье до старта первой задачи в очереди
    # проходили бы оба мимо проверки на "published" и ставили бы в очередь
    # ДВЕ задачи retry_article на одну статью.
    #
    # Решено чинить симметрично run(): переводить статью в "generating" здесь
    # же, синхронно, до apply_async. Стоимость фикса нулевая (то же
    # присваивание, что и так происходит секундами позже внутри build()), а
    # выгода не ограничивается двойным кликом — так же отклоняется retry
    # статьи, которая в этот момент уже собирается в рамках выполняющейся
    # run_batch (см. test_retry_rejects_article_already_generating).
    if article.status in ("published", "generating"):
        detail = ("статья уже выложена черновиком" if article.status == "published"
                  else "статья уже собирается — повторный запуск не требуется")
        raise HTTPException(400, detail)
    article.status = "generating"
    db.commit()
    # apply_async с вычисленными лимитами, а не delay() — находка №4 ревью
    # Task 17 (app/celery_app.py, _retry_time_limits выше): retry_article
    # вызывает build_for → ArticleBuilder.build() целиком, и реальный худший
    # случай (1462 + 366×N с, N — число картинок статьи) в 2-3 раза больше
    # глобального дефолта Celery (900/1080 с) уже при N=2. site может быть
    # None (сайт статьи удалён, Task 14, ON DELETE SET NULL) — тогда берём
    # reference_images=0: retry_article_sync сам обнаружит отсутствие сайта
    # и завершится почти мгновенно (находка №2 ревью Task 17), так что запас
    # времени здесь роли не играет.
    site = db.get(Site, article.site_id) if article.site_id is not None else None
    soft, hard = _retry_time_limits(site.reference_images if site else 0)
    retry_article.apply_async(args=[article.id], soft_time_limit=soft, time_limit=hard)
    return {"ok": True}


class RegenerateIn(BaseModel):
    text: bool = False
    images: bool = False
    cover: bool = False


@router.post("/articles/{article_id}/regenerate")
def regenerate(article_id: int, payload: RegenerateIn, db: Session = Depends(get_db),
               _user: User = Depends(get_current_user)):
    if not (payload.text or payload.images or payload.cover):
        raise HTTPException(400, "нужно выбрать хотя бы одну часть для перегенерации")
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(404, "статья не найдена")
    if article.status != "published":
        raise HTTPException(400, "перегенерация доступна только для опубликованных статей")
    # Тот же приём анти-гонки, что у run()/retry() выше: перевод в
    # "выполняется" синхронно, до apply_async, — второй быстрый клик
    # увидит уже True и не поставит вторую задачу в очередь.
    if article.regenerating:
        raise HTTPException(400, "перегенерация уже выполняется")
    article.regenerating = True
    db.commit()

    image_count = 0
    if payload.images:
        image_count = db.scalar(
            select(func.count(func.distinct(ArticleImage.position)))
            .where(ArticleImage.article_id == article.id, ArticleImage.kind == "content")
        ) or 0
    soft, hard = _regen_time_limits(text=payload.text, image_count=image_count,
                                    cover=payload.cover)
    regenerate_article.apply_async(
        args=[article.id],
        kwargs={"text": payload.text, "images": payload.images, "cover": payload.cover},
        soft_time_limit=soft, time_limit=hard)
    return {"ok": True}
