from app.models.article import Article, ArticleBatch, ArticleImage
from app.models.job import JobRun, LlmUsage


def test_batch_starts_in_topics_pending(db_session):
    batch = ArticleBatch(site_id=1, requested_count=10, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    assert batch.status == "topics_pending"


def test_article_starts_as_draft(db_session):
    batch = ArticleBatch(site_id=1, requested_count=1, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    article = Article(batch_id=batch.id, site_id=1, topic="Как выбрать фундамент")
    db_session.add(article)
    db_session.commit()
    assert article.status == "draft"
    assert article.remote_page_id is None


def test_batch_articles_relationship(db_session):
    batch = ArticleBatch(site_id=1, requested_count=2, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    db_session.add_all([
        Article(batch_id=batch.id, site_id=1, topic="Тема 1"),
        Article(batch_id=batch.id, site_id=1, topic="Тема 2"),
    ])
    db_session.commit()
    db_session.refresh(batch)
    assert [a.topic for a in batch.articles] == ["Тема 1", "Тема 2"]


def test_article_image_kinds(db_session):
    batch = ArticleBatch(site_id=1, requested_count=1, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    article = Article(batch_id=batch.id, site_id=1, topic="Т")
    db_session.add(article)
    db_session.commit()
    db_session.add_all([
        ArticleImage(article_id=article.id, kind="cover", position=0, prompt="p"),
        ArticleImage(article_id=article.id, kind="content", position=1, prompt="p"),
    ])
    db_session.commit()
    db_session.refresh(article)
    assert {i.kind for i in article.images} == {"cover", "content"}


def test_job_run_records_who_and_what(db_session):
    job = JobRun(kind="generate_topics", site_id=1, created_by_id=1,
                 params_json={"count": 10})
    db_session.add(job)
    db_session.commit()
    assert job.status == "running"
    assert job.params_json["count"] == 10


def test_llm_usage_links_to_job(db_session):
    job = JobRun(kind="build_article", site_id=1, created_by_id=1, params_json={})
    db_session.add(job)
    db_session.commit()
    db_session.add(LlmUsage(job_run_id=job.id, kind="image", model="openai/gpt-image-2",
                            cost=5.4))
    db_session.commit()
    db_session.refresh(job)
    assert job.usage[0].cost == 5.4


# --- дополнительные тесты: структурные решения, найденные при ревью плана ---


def test_article_image_kind_rejects_unknown_value(db_session):
    """kind — по сути перечисление cover|content (используется как literal
    в app/articles/builder.py, Task 16). Опечатка в этой строке в будущем
    коде (например, при доработке Task 17) не должна тихо лечь в таблицу —
    из неё потом читают через `next(i for i in article.images if i.kind ==
    "content" ...)`, и непойманный из-за опечатки кадр уронит сборку статьи
    необработанным StopIteration вместо внятной ошибки. CHECK на уровне БД
    ловит это сразу при INSERT, а не через несколько шагов конвейера."""
    from sqlalchemy.exc import IntegrityError

    batch = ArticleBatch(site_id=1, requested_count=1, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    article = Article(batch_id=batch.id, site_id=1, topic="Т")
    db_session.add(article)
    db_session.commit()
    db_session.add(ArticleImage(article_id=article.id, kind="banner", position=0))
    try:
        db_session.commit()
        assert False, "ожидался IntegrityError на недопустимом kind"
    except IntegrityError:
        db_session.rollback()


def test_duplicate_slug_within_site_is_rejected(db_session):
    """Два черновика с одинаковым slug на одном сайте целили бы в один и тот
    же url (builder.py: `articles_url_prefix + slug + '/'`). Живая проверка
    на сайте (_guard_duplicate_url, Task 16) защищает от этого только если
    список страниц сайта уже отдаёт свежесозданную страницу — при кэширующем
    или eventually-consistent списочном эндпоинте окно гонки есть. Частичный
    уникальный индекс — страховка в БД, а не замена проверки на сайте."""
    from sqlalchemy.exc import IntegrityError

    batch = ArticleBatch(site_id=1, requested_count=2, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    db_session.add(Article(batch_id=batch.id, site_id=1, topic="А", slug="chem-uteplit"))
    db_session.commit()
    db_session.add(Article(batch_id=batch.id, site_id=1, topic="Б", slug="chem-uteplit"))
    try:
        db_session.commit()
        assert False, "ожидался IntegrityError на дублирующемся slug в рамках сайта"
    except IntegrityError:
        db_session.rollback()


def test_empty_slug_does_not_collide_between_draft_articles(db_session):
    """Черновики до сборки (status=draft) имеют slug="" по умолчанию — их в
    партии может быть много одновременно (test_batch_articles_relationship).
    Частичный индекс должен игнорировать пустую строку, иначе второй черновик
    в партии не сохранился бы."""
    batch = ArticleBatch(site_id=1, requested_count=2, created_by_id=1)
    db_session.add(batch)
    db_session.commit()
    db_session.add_all([
        Article(batch_id=batch.id, site_id=1, topic="А"),
        Article(batch_id=batch.id, site_id=1, topic="Б"),
    ])
    db_session.commit()   # не должно бросить IntegrityError


def test_same_slug_allowed_on_different_sites(db_session):
    """Уникальность slug — в рамках сайта, а не глобальная: у двух разных
    сайтов разделы независимы, совпадение url между ними — не проблема."""
    batch1 = ArticleBatch(site_id=1, requested_count=1, created_by_id=1)
    batch2 = ArticleBatch(site_id=2, requested_count=1, created_by_id=1)
    db_session.add_all([batch1, batch2])
    db_session.commit()
    db_session.add_all([
        Article(batch_id=batch1.id, site_id=1, topic="А", slug="odna-tema"),
        Article(batch_id=batch2.id, site_id=2, topic="Б", slug="odna-tema"),
    ])
    db_session.commit()   # не должно бросить IntegrityError
