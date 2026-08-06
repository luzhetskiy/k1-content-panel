"""Сборка одной статьи: текст → картинки → загрузка → страница-черновик → обложка.

Разбит на шаги-методы, чтобы падение на любом из них попадало в error_text
статьи, а не роняло всю партию.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.factory import build_image_generator, build_text_client, image_params
from app.ai.images import ImageError
from app.ai.prompts import PromptError, render_prompt, resolve_prompt
from app.ai.text import LLMError
from app.ai.watermark import apply_watermark
from app.models.article import Article, ArticleImage
from app.models.job import LlmUsage
from app.models.site import Site
from app.sites.client import (
    ARTICLE_IMG_DIR,
    SLUG_LIMIT_ARTICLES,
    SLUG_LIMIT_PAGES,
    SiteAPIError,
    slugify,
)

COVER_CROP = "3:2"
CONTENT_CROP = "3:2"


class ArticleBuildError(RuntimeError):
    """Ошибка, обнаруженная самим билдером (не AI-провайдером и не сайтом),
    но которая всё равно обязана довести статью до status="failed", а не
    уронить сборку батча необработанным исключением. Пример: конфликт
    частичного уникального индекса uq_article_site_slug (см. _apply_body)."""


def image_filename(article_id: int, position: int) -> str:
    """position=0 — обложка, дальше контентные по порядку.

    Префикс "cp-article-" (а не просто "article_") — намеренно: на
    stroybaza-samara.ru в той же папке filemanager (ARTICLE_IMG_DIR) уже
    лежат файлы article_1-*.webp..article_6-*.webp, залитые старым CLI-
    пайплайном (execution/articles/, батч по номеру темы в манифесте, а не
    по id статьи). Т.к. filemanager при совпадении имени молча
    перезаписывает файл без суффикса, а id статей здесь — сквозной
    автоинкремент по всей таблице Article, три первые статьи нового сервиса
    на этом сайте получили id 4/5/6 и своими картинками затёрли/были
    затёрты картинками старых статей article_4/5/6 (пиломатериалы,
    пароизоляция, герметик) — баг был обнаружен на проде. Новый префикс не
    пересекается со старой схемой ни при каком id."""
    suffix = "cover" if position == 0 else str(position)
    return f"cp-article-{article_id}-{suffix}.webp"


def image_paths_for(article_id: int, count: int) -> list[str]:
    return [f"/media/{ARTICLE_IMG_DIR}{image_filename(article_id, i)}"
            for i in range(1, count + 1)]


class ArticleBuilder:
    def __init__(self, db: Session, article: Article, site: Site, text_client,
                 image_generator, site_client, image_params: dict,
                 watermark_bytes: bytes, job_run_id: int | None):
        self.db = db
        self.article = article
        self.site = site
        self.text_client = text_client
        self.image_generator = image_generator
        self.site_client = site_client
        self.image_params = image_params
        self.watermark_bytes = watermark_bytes
        self.job_run_id = job_run_id

    # --- публичный вход ---

    def build(self) -> None:
        self._set_status("generating")
        try:
            self._require_synced_reference()
            body = self._generate_body()
            self._apply_body(body)
            self._guard_duplicate_url()
            content_images = self._generate_content_images()
            self._upload_content_images(content_images)
            page = self._create_page()
            self._attach_cover(page["id"])
        except (LLMError, ImageError, SiteAPIError, PromptError,
                ArticleBuildError) as exc:
            # db.rollback() обязателен и здесь: если исключение — это
            # IntegrityError, пойманный и перевыброшенный из _apply_body как
            # ArticleBuildError, транзакция сессии уже отменена внутри
            # _apply_body до того, как долетело сюда (см. её комментарий) —
            # повторный rollback() на уже чистой сессии безопасен (no-op).
            # Для остальных типов исключений транзакция не портится, но
            # безусловный rollback() ничего не стоит и не полагается на то,
            # какой именно except-класс сработал.
            self.db.rollback()
            self.article.status = "failed"
            self.article.error_text = str(exc)
            self.db.commit()
            return
        self.article.status = "published"
        self.article.error_text = ""
        self.db.commit()

    # --- шаги ---

    def _set_status(self, status: str) -> None:
        self.article.status = status
        self.db.commit()

    def _slug_limit(self) -> int:
        return (SLUG_LIMIT_ARTICLES if self.site.publish_target == "articles"
                else SLUG_LIMIT_PAGES)

    def _require_synced_reference(self) -> None:
        """Проверка идёт до первого платного вызова: без эталона разметку взять
        неоткуда, и падать на этом после генерации картинок было бы обидно."""
        if not (self.site.reference_html and self.site.reference_images
                and self.site.articles_url_prefix):
            raise SiteAPIError(
                "эталон сайта не синхронизирован — нажми «Проверить и синхронизировать» "
                "на карточке сайта")

    def _image_count(self) -> int:
        """Сколько <img> в эталоне, столько картинок и генерируем."""
        return self.site.reference_images

    def _generate_body(self) -> dict:
        """Известное ограничение (найдено при ревью Task 16, не чинится
        здесь): TextClient.complete_json (app/ai/text.py) бросает LLMError
        ДО вызова self._usage(response), если json.loads провалился — то
        есть настоящий, уже оплаченный ответ провайдера отбрасывается вместе
        с исключением, и _record_usage ниже для этого вызова не выполнится
        никогда, потому что до него не доходит управление. Стоимость такого
        одного неудачного вызова текста нигде не осядет в LlmUsage, хотя
        RouterAI её уже учёл. Отличие от находки №1 (контентные картинки) —
        масштаб: там теряются N-1 успешных платных результатов из-за одного
        соседа, здесь — стоимость ровно одного неудачного вызова. Чинить
        значит менять сигнатуру TextClient.complete_json/LLMError (Task 7,
        уже закоммиченный и покрытый код с более широким радиусом влияния:
        его использует и /test-эндпоинт Task 13) — решено не делать этого
        в Task 16, а зафиксировать как принятый риск: разовая, а не
        системная потеря, и админ всё равно увидит failed-статью с текстом
        ошибки, просто без соответствующей строки расхода."""
        count = self._image_count()
        template = resolve_prompt(self.db, "article_body", self.site.id)
        prompt = render_prompt(template, {
            "topic": self.article.topic,
            "site_name": self.site.name,
            "site_description": self.site.site_description,
            "tone_of_voice": self.site.tone_of_voice,
            # Эталон берётся из кеша карточки — к сайту за ним не ходим.
            "reference_html": self.site.reference_html,
            "image_count": count,
            "image_paths": image_paths_for(self.article.id, count),
        })
        result = self.text_client.complete_json(prompt)
        self._record_usage("text", result.tokens_prompt, result.tokens_completion, result.cost)
        if not isinstance(result.data, dict) or "html" not in result.data:
            raise LLMError("модель вернула объект без поля html")
        return result.data

    def _apply_body(self, body: dict) -> None:
        self.article.title = body.get("title") or self.article.topic
        self.article.slug = slugify(self.article.title, limit=self._slug_limit())
        self.article.body_html = body["html"]
        self.article.meta_description = body.get("meta_description", "")
        self.article.meta_keywords = body.get("meta_keywords", "")
        try:
            self.db.commit()
        except IntegrityError as exc:
            # uq_article_site_slug (частичный индекс на (site_id, slug),
            # app/models/article.py) — реалистичный конфликт: фильтр дублей
            # тем (app/articles/topics.py, filter_duplicates) сам
            # документирует дыры (нет лемматизации словоформ), а slugify
            # дополнительно обрезает длину и схлопывает пунктуацию, так что
            # даже НЕ признанные дублями темы иногда дают одинаковый url.
            # После непойманного IntegrityError транзакция Postgres
            # переходит в aborted-состояние — любой следующий db.commit() в
            # этой же сессии тоже падает, пока не сделан rollback(). Проверено
            # эмпирически на живом Postgres 16 (docker compose up -d postgres,
            # миграции применены): без rollback() следующий commit() в той же
            # сессии падает sqlalchemy.exc.PendingRollbackError ("This
            # Session's transaction has been rolled back due to a previous
            # exception during flush... issue Session.rollback()") — так
            # SQLAlchemy перехватывает попытку продолжить работу в уже
            # прерванной транзакции раньше, чем голый psycopg успел бы отдать
            # InFailedSqlTransaction на стороне драйвера. С rollback() —
            # следующий commit() в той же сессии проходит штатно (тоже
            # проверено). Task 17 использует одну сессию на весь цикл по
            # статьям партии, поэтому rollback() здесь обязателен: без него не
            # только эта статья не долетит до status="failed", но и вся
            # партия развалится с этого места.
            self.db.rollback()
            target = f"{self.site.articles_url_prefix}{self.article.slug}/"
            raise ArticleBuildError(
                f"на этот сайт уже собирается или опубликована другая статья "
                f"с таким же адресом ({target}) — слаг совпал, попробуй "
                f"переформулировать тему") from exc

    def _guard_duplicate_url(self) -> None:
        """Дубль url означает повторный прогон той же темы — молча создавать
        вторую страницу нельзя."""
        target = f"{self.site.articles_url_prefix}{self.article.slug}/"
        taken = {p.get("url") for p in self.site_client.list_section_pages(
            self.site.articles_url_prefix)}
        if target in taken:
            raise SiteAPIError(f"страница {target} уже есть на сайте")

    def _image_prompt(self, key: str, variables: dict) -> str:
        rendered = render_prompt(resolve_prompt(self.db, key, self.site.id), variables)
        result = self.text_client.complete_text(rendered)
        self._record_usage("text", result.tokens_prompt, result.tokens_completion, result.cost)
        return result.text.strip()

    def _render_content_image(self, position: int, prompt: str):
        """Один платный вызов генерации + наложение знака. Вызывается из
        рабочего потока ThreadPoolExecutor — см. _generate_content_images."""
        result = self.image_generator.generate(
            prompt=prompt, size=self.image_params["size"],
            quality=self.image_params["quality"], crop=CONTENT_CROP)
        try:
            # Водяной знак — только на контентные картинки; обложка остаётся
            # чистой. apply_watermark(app/ai/watermark.py) на битом (не
            # изображение) watermark_bytes бросает PIL.UnidentifiedImageError
            # (наследник OSError) — файл существует (иначе build_for вообще
            # не дошёл бы сюда с непустыми байтами), но повреждён. Решение
            # (находка №6 ревью Task 16): НЕ трактовать это как «знака нет»
            # симметрично отсутствующему файлу — генерация уже оплачена, и
            # тихая публикация без знака подрывает то, ради чего знак вообще
            # ставится. Явная ошибка — админ должен узнать и поправить файл
            # на карточке сайта, а не обнаружить пропажу знака постфактум на
            # опубликованных картинках.
            data = apply_watermark(result.data, self.watermark_bytes)
        except OSError as exc:
            raise ImageError(
                f"файл водяного знака сайта повреждён (не изображение): {exc}"
            ) from exc
        return position, prompt, data, result.cost

    def _generate_content_images(self) -> list[tuple[int, bytes]]:
        count = self._image_count()
        prompts = [
            self._image_prompt("content_image", {
                "topic": self.article.topic,
                "paragraph": f"иллюстрация {position} из {count}",
                "image_style": self.site.image_style_prompt,
            })
            for position in range(1, count + 1)
        ]

        images: list[tuple[int, bytes]] = []
        first_error: Exception | None = None
        # Генерация занимает 40-140 с на кадр, поэтому идёт параллельно. Не
        # pool.map(): если одна генерация падает, list(pool.map(...)) бросает
        # исключение при первом же неудачном результате В ПОРЯДКЕ ПОДАЧИ
        # задач, а уже посчитанные результаты соседних успешных генераций
        # отбрасываются молча — сами HTTP-запросы к RouterAI за них уже
        # выполнились и, вероятно, уже оплачены (поток внутри
        # ThreadPoolExecutor не отменяется истечением with-блока, он просто
        # доработает и будет отброшен). as_completed отдаёт futures по мере
        # завершения, а не в порядке подачи, и здесь дожидаемся ВСЕХ
        # futures: для каждого успешного сразу пишем ArticleImage/LlmUsage
        # и коммитим, для проваленных — запоминаем первое исключение и
        # продолжаем ждать остальные, поднимая итоговую ошибку только после
        # того, как все futures обработаны. Обрыв ожидания раньше времени
        # (raise/break на первом же исключении из as_completed) не решил бы
        # проблему: остальные, ещё не завершившиеся потоки всё равно
        # доработают и, возможно, тоже вернут оплаченный результат, который
        # был бы потерян точно так же.
        with ThreadPoolExecutor(max_workers=self.image_params["workers"]) as pool:
            futures = [pool.submit(self._render_content_image, position, prompt)
                       for position, prompt in enumerate(prompts, start=1)]
            for future in as_completed(futures):
                try:
                    position, prompt, data, cost = future.result()
                except Exception as exc:  # noqa: BLE001 — см. комментарий выше
                    if first_error is None:
                        first_error = exc
                    continue
                self.db.add(ArticleImage(article_id=self.article.id, kind="content",
                                         position=position, prompt=prompt, cost=cost))
                self._record_usage("image", 0, 0, cost)
                self.db.commit()
                images.append((position, data))

        if first_error is not None:
            raise first_error
        images.sort(key=lambda item: item[0])
        return images

    def _upload_content_images(self, images: list[tuple[int, bytes]]) -> None:
        for position, data in images:
            filename = image_filename(self.article.id, position)
            path = self.site_client.upload_file(data, filename, ARTICLE_IMG_DIR)
            image = next(i for i in self.article.images
                         if i.kind == "content" and i.position == position)
            image.remote_path = path
        self.db.commit()

    def _create_page(self) -> dict:
        page = self.site_client.create_page(
            title=self.article.title,
            url=f"{self.site.articles_url_prefix}{self.article.slug}/",
            html=self.article.body_html,
            parent_id=self.site.articles_parent_id,
            meta_description=self.article.meta_description,
            meta_keywords=self.article.meta_keywords,
        )
        self.article.remote_page_id = page["id"]
        self.article.remote_url = f"{self.site.base_url}{page.get('url', '')}"
        self.db.commit()
        return page

    def _attach_cover(self, page_id: int) -> None:
        style = (self.site.cover_style_prompt if self.site.cover_mode == "prompt"
                 else "в стиле уже существующих обложек этого сайта")
        prompt = self._image_prompt("cover", {"topic": self.article.topic,
                                              "cover_style": style})
        result = self.image_generator.generate(
            prompt=prompt, size=self.image_params["size"],
            quality=self.image_params["quality"], crop=COVER_CROP)
        filename = image_filename(self.article.id, 0)
        self.site_client.set_page_cover(page_id, result.data, filename)
        self.db.add(ArticleImage(article_id=self.article.id, kind="cover", position=0,
                                 prompt=prompt, remote_path=filename, cost=result.cost))
        self._record_usage("image", 0, 0, result.cost)
        self.db.commit()

    def _record_usage(self, kind: str, tokens_prompt: int, tokens_completion: int,
                      cost: float) -> None:
        """model берётся из источника, СООТВЕТСТВУЮЩЕГО kind, а не всегда с
        text_client (найдено при ревью Task 16: старая версия писала модель
        текстового клиента даже для kind="image" — журнал расходов
        (LlmUsage, app/models/job.py) существует именно для того, чтобы
        «расход надо видеть до того, как он станет сюрпризом», и неверная
        модель в строке журнала подрывает ровно эту цель)."""
        if self.job_run_id is None:
            return
        source = self.image_generator if kind == "image" else self.text_client
        self.db.add(LlmUsage(job_run_id=self.job_run_id, kind=kind,
                             model=getattr(source, "model", ""),
                             tokens_prompt=tokens_prompt,
                             tokens_completion=tokens_completion, cost=cost))


def build_for(db: Session, article: Article, site: Site, site_client,
              job_run_id: int | None) -> None:
    """Сборка билдера из настроек БД — точка входа для Celery-задачи.

    Известный риск (найдено при ревью Task 16, не чинится здесь): сборка
    клиентов (build_text_client/build_image_generator/image_params) идёт
    ДО входа в try-блок ArticleBuilder.build(), то есть если из-за
    незаполненного ключа RouterAI или неверного ENCRYPTION_KEY любая из
    них бросит AIConfigError (Task 13, app/ai/factory.py) — это исключение
    вылетит из build_for() непойманным, наружу за пределы try/except
    build()'а. По инварианту Task 17 (run_batch_sync не должен ронять весь
    батч из-за одной статьи) это выглядит как нарушение. Но AIConfigError —
    ошибка конфигурации панели, ОДНА И ТА ЖЕ для всех статей партии (ключ
    либо задан, либо нет), а не отказ, специфичный для конкретной статьи;
    осмысленное место проверки — один раз ДО цикла по статьям в
    run_batch_sync (Task 17, ещё не реализован), а не здесь на каждую
    статью по отдельности. Оставлено как открытый риск с конкретным
    сценарием для Task 17, чтобы не забыть при её реализации."""
    watermark = b""
    if site.watermark_path:
        try:
            with open(site.watermark_path, "rb") as f:
                watermark = f.read()
        except OSError:
            watermark = b""

    ArticleBuilder(
        db=db, article=article, site=site,
        text_client=build_text_client(db),
        image_generator=build_image_generator(db),
        site_client=site_client,
        image_params=image_params(db),
        watermark_bytes=watermark,
        job_run_id=job_run_id,
    ).build()
