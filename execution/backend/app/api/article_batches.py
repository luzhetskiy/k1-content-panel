from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.article import Article, ArticleBatch, ArticleImage
from app.models.site import Site
from app.models.user import User
from app.tasks import generate_topics, regenerate_article_images, retry_article, run_batch

router = APIRouter(prefix="/api", tags=["articles"])

EDITABLE_STATUSES = {"topics_pending", "topics_review", "failed"}


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
    images_regenerating: bool


class BatchOut(BaseModel):
    id: int
    site_id: int
    site_name: str
    site_domain: str
    requested_count: int
    status: str
    error_text: str
    created_at: datetime
    articles: list[ArticleOut] = []


def _to_out(db: Session, batch: ArticleBatch) -> BatchOut:
    site = db.get(Site, batch.site_id)
    return BatchOut(
        id=batch.id, site_id=batch.site_id,
        site_name=site.name if site else "—",
        site_domain=site.domain if site else "—",
        requested_count=batch.requested_count, status=batch.status,
        error_text=batch.error_text, created_at=batch.created_at,
        articles=[ArticleOut(id=a.id, topic=a.topic, title=a.title, status=a.status,
                             remote_url=a.remote_url, error_text=a.error_text,
                             images_regenerating=a.images_regenerating)
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


# Перегенерация не пересобирает текст и не создаёт страницу заново —
# бюджет считается только по картинкам: N последовательных текстовых
# промптов иллюстраций (_RETRY_PER_IMAGE_SECONDS каждый) плюс одна
# параллельная пачка генерации самих картинок (365 с, см. app/ai/images.py)
# плюс запас на загрузку файлов и update_page_text.
_REGEN_OVERHEAD_SECONDS = 300
_REGEN_IMAGE_BATCH_SECONDS = 365


def _regen_time_limits(image_count: int) -> tuple[int, int]:
    soft = (_REGEN_OVERHEAD_SECONDS + _RETRY_PER_IMAGE_SECONDS * image_count
           + _REGEN_IMAGE_BATCH_SECONDS)
    return soft, soft + TIME_LIMIT_GAP_SECONDS


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
        raise HTTPException(400, "партия уже выполняется")
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
    db.commit()
    # Лимит времени вычисляется здесь, а не берётся из глобальной настройки
    # Celery: партия идёт последовательно, и её длительность пропорциональна
    # числу статей. Глухой статический лимит обрывал бы работу на середине —
    # часть статей опубликована, часть нет.
    soft, hard = _batch_time_limits(len(batch.articles))
    run_batch.apply_async(args=[batch.id], soft_time_limit=soft, time_limit=hard)
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


@router.post("/articles/{article_id}/regenerate-images")
def regenerate_images(article_id: int, db: Session = Depends(get_db),
                      _user: User = Depends(get_current_user)):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(404, "статья не найдена")
    if article.status != "published":
        raise HTTPException(
            400, "перегенерация картинок доступна только для опубликованных статей")
    # Тот же приём анти-гонки, что у run()/retry() выше: перевод в
    # "выполняется" синхронно, до apply_async, — второй быстрый клик
    # увидит уже True и не поставит вторую задачу в очередь.
    if article.images_regenerating:
        raise HTTPException(400, "перегенерация картинок уже выполняется")
    article.images_regenerating = True
    db.commit()

    image_count = db.scalar(
        select(func.count(func.distinct(ArticleImage.position)))
        .where(ArticleImage.article_id == article.id, ArticleImage.kind == "content")
    ) or 0
    soft, hard = _regen_time_limits(image_count)
    regenerate_article_images.apply_async(args=[article.id], soft_time_limit=soft,
                                          time_limit=hard)
    return {"ok": True}
