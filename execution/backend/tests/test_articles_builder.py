import json
from types import SimpleNamespace

import pytest

from app.ai.images import ImageError, ImageResult
from app.ai.text import JsonResult, TextResult
from app.articles.builder import ArticleBuilder, image_filename, image_paths_for


class FakeTextClient:
    def __init__(self, body: dict):
        self.body = body
        self.prompts = []

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        return JsonResult(self.body, 100, 200, 0.4)

    def complete_text(self, prompt):
        self.prompts.append(prompt)
        return TextResult("промпт картинки", 10, 20, 0.05)


class FakeImageGenerator:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, size, quality, crop):
        self.calls.append(prompt)
        return ImageResult(data=b"webp-bytes", size=(1600, 1066), cost=5.4, seconds=60)


class FakeSiteClient:
    def __init__(self):
        self.uploaded = []
        self.created = None
        self.cover = None
        self.fetched_pages = []

    def get_page(self, page_id):
        self.fetched_pages.append(page_id)
        return {"id": page_id, "text": "<article class='post'><p>эталон</p></article>"}

    def list_section_pages(self, prefix):
        return [{"id": 1, "title": "Старая статья", "url": prefix + "staraya/"}]

    def upload_file(self, data, filename, upload_to):
        self.uploaded.append(filename)
        return f"/media/{upload_to}{filename}"

    def create_page(self, title, url, html, parent_id, meta_description, meta_keywords):
        self.created = dict(title=title, url=url, html=html, parent_id=parent_id)
        return {"id": 501, "url": url}

    def set_page_cover(self, page_id, image_bytes, filename):
        self.cover = (page_id, filename)
        return "/media/staticpages/images/" + filename


@pytest.fixture
def prepared(db_session, admin):
    from app.models.article import Article, ArticleBatch
    from app.models.site import Site
    from app.seed import seed_prompts

    seed_prompts(db_session)
    site = Site(name="Стройбаза", domain="x.ru", base_url="https://x.ru",
                api_token_enc="e", articles_parent_id=25,
                articles_url_prefix="/poleznye-stati/",
                site_description="Стройбаза в Самаре", tone_of_voice="практичный",
                reference_article_id=312,
                reference_html="<article class='post'><p>эталон</p><img><img></article>",
                reference_images=2,
                image_style_prompt="фото стройки", cover_style_prompt="обложка")
    db_session.add(site)
    db_session.commit()
    batch = ArticleBatch(site_id=site.id, requested_count=1,
                         created_by_id=admin.id, status="running")
    db_session.add(batch)
    db_session.commit()
    article = Article(batch_id=batch.id, site_id=site.id, topic="Чем утеплить дом")
    db_session.add(article)
    db_session.commit()
    return SimpleNamespace(site=site, batch=batch, article=article)


def make_builder(db_session, prepared, site_client=None, body=None):
    body = body or {
        "title": "Чем утеплить каркасный дом",
        "html": "<article class='post'><p>Текст</p>"
                "<img src='/media/uploads/article-img/article_1-1.webp'>"
                "<img src='/media/uploads/article-img/article_1-2.webp'></article>",
        "meta_description": "описание",
        "meta_keywords": "утепление",
    }
    return ArticleBuilder(
        db=db_session,
        article=prepared.article,
        site=prepared.site,
        text_client=FakeTextClient(body),
        image_generator=FakeImageGenerator(),
        site_client=site_client or FakeSiteClient(),
        image_params={"size": "1536x1024", "quality": "medium", "workers": 2},
        watermark_bytes=b"",
        job_run_id=None,
    )


def test_image_filename_is_deterministic():
    assert image_filename(7, 0) == "cp-article-7-cover.webp"
    assert image_filename(7, 2) == "cp-article-7-2.webp"


def test_image_filename_adds_version_suffix_from_second_round():
    assert image_filename(7, 2, version=1) == "cp-article-7-2.webp"
    assert image_filename(7, 2, version=2) == "cp-article-7-2_v2.webp"
    assert image_filename(7, 0, version=3) == "cp-article-7-cover_v3.webp"


def test_image_paths_use_article_img_dir():
    assert image_paths_for(7, 2) == [
        "/media/uploads/article-img/cp-article-7-1.webp",
        "/media/uploads/article-img/cp-article-7-2.webp",
    ]


def test_build_sets_title_slug_and_html(db_session, prepared):
    builder = make_builder(db_session, prepared)
    builder.build()
    assert prepared.article.title == "Чем утеплить каркасный дом"
    assert prepared.article.slug == "chem-uteplit-karkasnyy-dom"
    assert "<article" in prepared.article.body_html


def test_build_publishes_draft_and_records_remote_id(db_session, prepared):
    site_client = FakeSiteClient()
    make_builder(db_session, prepared, site_client).build()
    assert prepared.article.status == "published"
    assert prepared.article.remote_page_id == 501
    assert site_client.created["url"] == "/poleznye-stati/chem-uteplit-karkasnyy-dom/"
    assert site_client.created["parent_id"] == 25


def test_build_uploads_content_images_and_cover(db_session, prepared):
    site_client = FakeSiteClient()
    make_builder(db_session, prepared, site_client).build()
    # 2 контентные картинки идут через filemanager, обложка — отдельным PATCH
    assert site_client.uploaded == ["cp-article-1-1.webp", "cp-article-1-2.webp"]
    assert site_client.cover == (501, "cp-article-1-cover.webp")


def test_reference_comes_from_cache_without_network(db_session, prepared):
    """Эталон лежит в карточке сайта — за ним на сайт не ходим."""
    site_client = FakeSiteClient()
    builder = make_builder(db_session, prepared, site_client)
    builder.build()
    assert "эталон" in builder.text_client.prompts[0]
    assert site_client.fetched_pages == []


def test_image_count_follows_reference(db_session, prepared):
    """Сколько <img> в эталоне, столько картинок и генерируется — отдельной
    настройки «сколько картинок» нет."""
    prepared.site.reference_images = 3
    db_session.commit()
    site_client = FakeSiteClient()
    builder = make_builder(db_session, prepared, site_client)
    builder.build()
    assert site_client.uploaded == ["cp-article-1-1.webp", "cp-article-1-2.webp",
                                    "cp-article-1-3.webp"]
    assert "3 иллюстраций" in builder.text_client.prompts[0]


def test_unsynced_site_fails_early(db_session, prepared):
    """Без синхронизации эталона генерировать нечего — падаем до трат на картинки."""
    prepared.site.reference_html = ""
    prepared.site.reference_images = 0
    db_session.commit()
    builder = make_builder(db_session, prepared)
    builder.build()
    assert prepared.article.status == "failed"
    assert "синхронизирован" in prepared.article.error_text
    assert builder.image_generator.calls == []


def test_site_profile_reaches_the_prompt(db_session, prepared):
    builder = make_builder(db_session, prepared)
    builder.build()
    body_prompt = builder.text_client.prompts[0]
    assert "Стройбаза в Самаре" in body_prompt
    assert "практичный" in body_prompt


def test_slug_is_truncated_to_limit(db_session, prepared):
    long_body = {
        "title": "Очень длинный заголовок " * 10,
        "html": "<p>x</p>", "meta_description": "", "meta_keywords": "",
    }
    builder = make_builder(db_session, prepared, body=long_body)
    builder.build()
    assert len(prepared.article.slug) <= 70


def test_duplicate_url_is_skipped_not_recreated(db_session, prepared):
    class ExistingUrlClient(FakeSiteClient):
        def list_section_pages(self, prefix):
            return [{"id": 9, "title": "Старая",
                     "url": prefix + "chem-uteplit-karkasnyy-dom/"}]

    site_client = ExistingUrlClient()
    make_builder(db_session, prepared, site_client).build()
    assert site_client.created is None
    assert prepared.article.status == "failed"
    assert "уже есть" in prepared.article.error_text


def test_failure_is_recorded_on_article(db_session, prepared):
    class BrokenClient(FakeSiteClient):
        def create_page(self, **kwargs):
            from app.sites.client import SiteAPIError

            raise SiteAPIError("создание страницы: HTTP 403: Forbidden")

    make_builder(db_session, prepared, BrokenClient()).build()
    assert prepared.article.status == "failed"
    assert "403" in prepared.article.error_text


def test_model_returning_non_json_marks_article_failed(db_session, prepared):
    builder = make_builder(db_session, prepared)

    def broken_json(prompt):
        from app.ai.text import LLMError

        raise LLMError("модель вернула не JSON: извините")

    builder.text_client.complete_json = broken_json
    builder.build()
    assert prepared.article.status == "failed"
    assert "не JSON" in prepared.article.error_text


# --- Обязательные находки ревью Task 16 ---


class SequencedTextClient(FakeTextClient):
    """complete_text нумерует свои вызовы в тексте результата — так дальний
    FakeImageGenerator по содержимому prompt узнаёт, для какой по счёту
    иллюстрации (= какой позиции) он вызван, не завися от того, в каком
    порядке потоки ThreadPoolExecutor реально стартуют."""

    def __init__(self, body):
        super().__init__(body)
        self._text_calls = 0

    def complete_text(self, prompt):
        self.prompts.append(prompt)
        self._text_calls += 1
        return TextResult(f"промпт-{self._text_calls}", 10, 20, 0.05)


class PartialFailureImageGenerator:
    """Один запрос (по номеру в prompt) падает с ImageError, остальные —
    успешны. model задан явно для находки №3 (путаница модели в LlmUsage)."""

    model = "openai/gpt-image-2"

    def __init__(self, fail_on: str):
        self.calls = []
        self.fail_on = fail_on

    def generate(self, prompt, size, quality, crop):
        self.calls.append(prompt)
        if prompt.endswith(self.fail_on):
            raise ImageError("сорвался провайдер: 500")
        return ImageResult(data=b"webp-bytes", size=(1600, 1066), cost=5.4, seconds=1)


def test_content_image_partial_failure_still_records_successful_siblings(db_session, prepared):
    """Находка №1 ревью Task 16: если одна из параллельных генераций
    контентных картинок падает, уже успешно (и оплачено) сгенерированные
    соседние картинки всё равно должны попасть в ArticleImage/LlmUsage, а не
    потеряться вместе с исключением. Мутационно проверено (см. отчёт
    задачи): при возврате к `list(pool.map(...))` этот тест падает — ни
    одна ArticleImage не записывается вообще, включая успешные."""
    from app.models.article import ArticleImage
    from app.models.job import JobRun, LlmUsage

    prepared.site.reference_images = 3
    db_session.commit()
    job = JobRun(kind="build_article", site_id=prepared.site.id,
                created_by_id=None)
    db_session.add(job)
    db_session.commit()

    body = {
        "title": "Чем утеплить каркасный дом", "html": "<p>x</p>",
        "meta_description": "", "meta_keywords": "",
    }
    text_client = SequencedTextClient(body)
    image_generator = PartialFailureImageGenerator(fail_on="2")
    builder = ArticleBuilder(
        db=db_session, article=prepared.article, site=prepared.site,
        text_client=text_client, image_generator=image_generator,
        site_client=FakeSiteClient(),
        image_params={"size": "1536x1024", "quality": "medium", "workers": 3},
        watermark_bytes=b"", job_run_id=job.id,
    )

    builder.build()

    assert prepared.article.status == "failed"
    assert "сорвался провайдер" in prepared.article.error_text

    images = db_session.query(ArticleImage).filter_by(
        article_id=prepared.article.id, kind="content").all()
    assert sorted(i.position for i in images) == [1, 3]
    assert all(i.cost == 5.4 for i in images)

    usage = db_session.query(LlmUsage).filter_by(job_run_id=job.id, kind="image").all()
    assert len(usage) == 2
    assert all(u.cost == 5.4 for u in usage)


def test_duplicate_slug_within_same_site_marks_second_failed_without_crashing(
        db_session, prepared):
    """Находка №2 ревью Task 16: два Article с топиками, слагифицирующимися
    в одинаковый url (частичный индекс uq_article_site_slug), не должны
    ронять build() необработанным IntegrityError — вторая статья обязана
    остаться failed, а сессия — оставаться пригодной для следующего
    commit (иначе Task 17's цикл по статьям партии развалился бы целиком).
    Мутационно проверено (см. отчёт задачи): без try/except IntegrityError
    в _apply_body этот тест падает с необработанным IntegrityError."""
    from app.models.article import Article

    second = Article(batch_id=prepared.batch.id, site_id=prepared.site.id,
                     topic="Чем ещё утеплить дом")
    db_session.add(second)
    db_session.commit()

    same_body = {
        "title": "Чем утеплить каркасный дом", "html": "<p>x</p>",
        "meta_description": "", "meta_keywords": "",
    }
    make_builder(db_session, prepared, body=same_body).build()
    assert prepared.article.status == "published"
    assert prepared.article.slug == "chem-uteplit-karkasnyy-dom"

    second_builder = ArticleBuilder(
        db=db_session, article=second, site=prepared.site,
        text_client=FakeTextClient(same_body), image_generator=FakeImageGenerator(),
        site_client=FakeSiteClient(),
        image_params={"size": "1536x1024", "quality": "medium", "workers": 2},
        watermark_bytes=b"", job_run_id=None,
    )
    second_builder.build()

    assert second.status == "failed"
    assert "уже собирается" in second.error_text

    # Сессия не осталась в сорванной транзакции — следующий commit проходит.
    prepared.site.tone_of_voice = "проверка после отката"
    db_session.commit()
    assert prepared.site.tone_of_voice == "проверка после отката"


def test_llm_usage_model_matches_kind_not_always_text_client(db_session, prepared):
    """Находка №3 ревью Task 16: LlmUsage.model для kind="image" обязан
    браться у image_generator, а не у text_client — иначе журнал расходов
    (app/models/job.py) показывает неправильную модель для картинок.
    Мутационно проверено (см. отчёт задачи): при возврате к
    `getattr(self.text_client, "model", "")` для всех kind этот тест
    падает — LlmUsage(kind="image").model оказывается текстовой моделью."""
    from app.models.job import JobRun, LlmUsage

    class NamedTextClient(FakeTextClient):
        model = "anthropic/claude-sonnet-4-6"

    class NamedImageGenerator(FakeImageGenerator):
        model = "openai/gpt-image-2"

    job = JobRun(kind="build_article", site_id=prepared.site.id, created_by_id=None)
    db_session.add(job)
    db_session.commit()

    body = {
        "title": "Чем утеплить каркасный дом", "html": "<p>x</p>",
        "meta_description": "", "meta_keywords": "",
    }
    builder = ArticleBuilder(
        db=db_session, article=prepared.article, site=prepared.site,
        text_client=NamedTextClient(body), image_generator=NamedImageGenerator(),
        site_client=FakeSiteClient(),
        image_params={"size": "1536x1024", "quality": "medium", "workers": 2},
        watermark_bytes=b"", job_run_id=job.id,
    )
    builder.build()

    assert prepared.article.status == "published"
    image_rows = db_session.query(LlmUsage).filter_by(job_run_id=job.id, kind="image").all()
    text_rows = db_session.query(LlmUsage).filter_by(job_run_id=job.id, kind="text").all()
    assert image_rows and all(r.model == "openai/gpt-image-2" for r in image_rows)
    assert text_rows and all(r.model == "anthropic/claude-sonnet-4-6" for r in text_rows)
