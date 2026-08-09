import pytest


@pytest.fixture
def site_id(admin_client, db_session):
    from app.models.site import Site

    site = Site(name="Стройбаза", domain="x.ru", base_url="https://x.ru",
                api_token_enc="e", articles_parent_id=25,
                articles_url_prefix="/poleznye-stati/",
                reference_html="<p>эталон</p><img><img>", reference_images=2)
    db_session.add(site)
    db_session.commit()
    return site.id


@pytest.fixture
def no_celery(monkeypatch):
    sent = []
    monkeypatch.setattr("app.api.article_batches.generate_topics.delay",
                        lambda batch_id: sent.append(("topics", batch_id)) or
                        type("R", (), {"id": "task-1"})())
    # run_batch и retry_article ставятся через apply_async с вычисленными
    # лимитами времени, а не через delay (находка №4 ревью Task 17, см.
    # app/celery_app.py и _retry_time_limits ниже) — подменяем оба.
    monkeypatch.setattr("app.api.article_batches.run_batch.apply_async",
                        lambda args, **kwargs: sent.append(("run", args[0], kwargs)) or
                        type("R", (), {"id": "task-2"})())
    monkeypatch.setattr("app.api.article_batches.retry_article.apply_async",
                        lambda args, **kwargs: sent.append(("retry", args[0], kwargs)) or
                        type("R", (), {"id": "task-3"})())
    monkeypatch.setattr(
        "app.api.article_batches.regenerate_article_images.apply_async",
        lambda args, **kwargs: sent.append(("regenerate", args[0], kwargs)) or
        type("R", (), {"id": "task-4"})())
    return sent


# TODO (Task 18, не покрыто здесь — оставлено явным напоминанием, а не тихим
# пробелом): нужен тест на _retry_time_limits, аналогичный
# test_batch_time_limits_grow_with_article_count ниже — соответствие росту
# reference_images, и тест на сам эндпоинт /articles/{id}/retry, что он
# зовёт retry_article.apply_async(...) с лимитами, посчитанными по
# site.reference_images статьи, а не голый .delay().


def test_batch_time_limits_grow_with_article_count():
    """Лимит партии считается от числа статей: иначе большая партия
    обрывается на середине по глухому статическому лимиту."""
    from app.api.article_batches import _batch_time_limits

    soft_one, hard_one = _batch_time_limits(1)
    soft_many, _ = _batch_time_limits(50)
    assert soft_many > soft_one
    assert hard_one > soft_one          # мягкий раньше жёсткого
    assert _batch_time_limits(10_000)[0] <= 6 * 60 * 60   # потолок держит


def test_manager_creates_batch(manager_client, site_id, no_celery):
    resp = manager_client.post("/api/article-batches",
                               json={"site_id": site_id, "count": 5})
    assert resp.status_code == 200
    assert resp.json()["status"] == "topics_pending"
    assert no_celery == [("topics", resp.json()["id"])]


def test_batch_requires_auth(client, site_id):
    assert client.post("/api/article-batches",
                       json={"site_id": site_id, "count": 5}).status_code == 401


def test_unknown_site_rejected(manager_client, no_celery):
    resp = manager_client.post("/api/article-batches", json={"site_id": 999, "count": 5})
    assert resp.status_code == 404


def test_count_is_bounded(manager_client, site_id, no_celery):
    assert manager_client.post("/api/article-batches",
                               json={"site_id": site_id, "count": 0}).status_code == 422
    assert manager_client.post("/api/article-batches",
                               json={"site_id": site_id, "count": 51}).status_code == 422


def test_batch_detail_lists_articles(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 2}).json()["id"]
    db_session.add(Article(batch_id=batch_id, site_id=site_id, topic="Тема А"))
    db_session.commit()

    body = manager_client.get(f"/api/article-batches/{batch_id}").json()
    assert body["site_name"] == "Стройбаза"
    assert [a["topic"] for a in body["articles"]] == ["Тема А"]


def test_topics_can_be_edited_before_run(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 2}).json()["id"]
    db_session.add(Article(batch_id=batch_id, site_id=site_id, topic="Старая тема"))
    db_session.commit()

    resp = manager_client.put(f"/api/article-batches/{batch_id}/topics",
                              json={"topics": ["Новая А", "Новая Б"]})
    assert resp.status_code == 200
    assert [a["topic"] for a in resp.json()["articles"]] == ["Новая А", "Новая Б"]


def test_topics_cannot_be_edited_after_run(manager_client, db_session, site_id, no_celery):
    from app.models.article import ArticleBatch

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 2}).json()["id"]
    db_session.get(ArticleBatch, batch_id).status = "running"
    db_session.commit()

    resp = manager_client.put(f"/api/article-batches/{batch_id}/topics",
                              json={"topics": ["Поздно"]})
    assert resp.status_code == 400


def test_run_requires_topics(manager_client, site_id, no_celery):
    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 2}).json()["id"]
    resp = manager_client.post(f"/api/article-batches/{batch_id}/run")
    assert resp.status_code == 400
    assert "тем" in resp.json()["detail"]


def test_run_starts_task(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    db_session.add(Article(batch_id=batch_id, site_id=site_id, topic="Тема"))
    db_session.commit()

    resp = manager_client.post(f"/api/article-batches/{batch_id}/run")
    assert resp.status_code == 200
    # Дефект черновика теста (найден при прогоне Task 18): фикстура no_celery
    # кладёт в sent 3-элементные кортежи ("run", batch_id, kwargs) — kwargs
    # нужен для будущей проверки лимитов времени (см. TODO выше), — а не
    # 2-элементные. `("run", batch_id) in no_celery` не совпадёт никогда ни
    # при каком поведении кода: 2-кортеж не равен 3-кортежу. Проверено:
    # с этой строкой тест падает даже на правильно работающем run().
    # Сравниваем по первым двум элементам, не трогая форму фикстуры.
    assert any(entry[:2] == ("run", batch_id) for entry in no_celery)


def test_jobs_list_shows_cost(manager_client, db_session, site_id):
    from app.models.job import JobRun, LlmUsage

    job = JobRun(kind="run_batch", site_id=site_id, params_json={}, status="ok")
    db_session.add(job)
    db_session.commit()
    db_session.add_all([LlmUsage(job_run_id=job.id, kind="image", cost=5.4),
                        LlmUsage(job_run_id=job.id, kind="text", cost=0.6)])
    db_session.commit()

    body = manager_client.get("/api/jobs").json()
    assert body[0]["kind"] == "run_batch"
    assert body[0]["cost"] == pytest.approx(6.0)


# --- находка №1 ревью Task 18: save_topics не должна стирать историю уже
# опубликованных статей. Партия становится "failed" не только когда генерация
# тем провалилась (тогда Article нет вообще), но и когда run_batch_sync
# обрывается посреди сборки — часть статей к этому моменту уже реально
# status="published" с заполненными remote_page_id/remote_url. ---

def test_save_topics_rejects_batch_with_published_article(manager_client, db_session,
                                                           site_id, no_celery):
    from app.models.article import Article, ArticleBatch

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 2}).json()["id"]
    db_session.add_all([
        Article(batch_id=batch_id, site_id=site_id, topic="Уже вышла",
               status="published", remote_page_id=42, remote_url="https://x.ru/a/"),
        Article(batch_id=batch_id, site_id=site_id, topic="Не вышла", status="failed"),
    ])
    batch = db_session.get(ArticleBatch, batch_id)
    batch.status = "failed"
    db_session.commit()

    resp = manager_client.put(f"/api/article-batches/{batch_id}/topics",
                              json={"topics": ["Новая тема"]})

    assert resp.status_code == 400
    # Опубликованная статья должна остаться нетронутой в БД — это и есть
    # журнал того, что реально появилось на сайте (Task 14).
    db_session.expire_all()
    remaining = db_session.get(ArticleBatch, batch_id).articles
    assert {a.topic for a in remaining} == {"Уже вышла", "Не вышла"}
    published = next(a for a in remaining if a.status == "published")
    assert published.remote_page_id == 42
    assert published.remote_url == "https://x.ru/a/"


# --- находка №2 ревью Task 18: run() и retry() должны переводить статус
# синхронно, до постановки задачи в очередь, — иначе повторный вызов до
# реального старта задачи в Celery проскакивает мимо проверки и ставит в
# очередь вторую задачу на ту же работу (двойная оплата LLM/картинок). ---

def test_run_twice_dispatches_run_batch_once(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    db_session.add(Article(batch_id=batch_id, site_id=site_id, topic="Тема"))
    db_session.commit()

    first = manager_client.post(f"/api/article-batches/{batch_id}/run")
    second = manager_client.post(f"/api/article-batches/{batch_id}/run")

    assert first.status_code == 200
    assert second.status_code == 400
    run_dispatches = [entry for entry in no_celery if entry[0] == "run"]
    assert len(run_dispatches) == 1


def test_retry_rejects_article_already_generating(manager_client, db_session,
                                                   site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема", status="failed")
    db_session.add(article)
    db_session.commit()

    first = manager_client.post(f"/api/articles/{article.id}/retry")
    second = manager_client.post(f"/api/articles/{article.id}/retry")

    assert first.status_code == 200
    assert second.status_code == 400
    retry_dispatches = [entry for entry in no_celery if entry[0] == "retry"]
    assert len(retry_dispatches) == 1


# --- находка №3 ревью Task 18: LlmUsage.cost — float, сумма нескольких
# значений накапливает двоичную погрешность. round(x, 2) обязателен на
# стороне ответа API (docstring app/models/job.py). pytest.approx в
# test_jobs_list_shows_cost этого не поймал бы — нужны конкретные числа. ---

def test_jobs_cost_is_rounded_for_display(manager_client, db_session, site_id):
    from app.models.job import JobRun, LlmUsage

    # sum([0.1, 0.2]) в Python — 0.30000000000000004, не 0.3: классический
    # пример погрешности двоичного float. Без round(x, 2) в ответе API ушло
    # бы длинное число вместо чистого 0.3.
    assert sum([0.1, 0.2]) != 0.3
    job = JobRun(kind="run_batch", site_id=site_id, params_json={}, status="ok")
    db_session.add(job)
    db_session.commit()
    db_session.add_all([LlmUsage(job_run_id=job.id, kind="text", cost=0.1),
                        LlmUsage(job_run_id=job.id, kind="text", cost=0.2)])
    db_session.commit()

    body = manager_client.get("/api/jobs").json()
    assert body[0]["cost"] == 0.3


def test_regen_time_limits_grow_with_image_count():
    from app.api.article_batches import _regen_time_limits

    soft_one, hard_one = _regen_time_limits(1)
    soft_many, _ = _regen_time_limits(5)
    assert soft_many > soft_one
    assert hard_one > soft_one


def test_regenerate_images_unknown_article_404(manager_client, no_celery):
    resp = manager_client.post("/api/articles/999/regenerate-images")
    assert resp.status_code == 404


def test_regenerate_images_requires_published_article(manager_client, db_session,
                                                       site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема", status="draft")
    db_session.add(article)
    db_session.commit()

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate-images")
    assert resp.status_code == 400


def test_regenerate_images_starts_task_for_published_article(manager_client, db_session,
                                                              site_id, no_celery):
    from app.models.article import Article, ArticleImage

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501)
    db_session.add(article)
    db_session.commit()
    db_session.add(ArticleImage(article_id=article.id, kind="content", position=1,
                                remote_path="/media/x/cp-article-1-1.webp"))
    db_session.commit()

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate-images")

    assert resp.status_code == 200
    assert any(entry[:2] == ("regenerate", article.id) for entry in no_celery)
    db_session.refresh(article)
    assert article.images_regenerating is True


def test_regenerate_images_twice_dispatches_once(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501)
    db_session.add(article)
    db_session.commit()

    first = manager_client.post(f"/api/articles/{article.id}/regenerate-images")
    second = manager_client.post(f"/api/articles/{article.id}/regenerate-images")

    assert first.status_code == 200
    assert second.status_code == 400
    dispatches = [entry for entry in no_celery if entry[0] == "regenerate"]
    assert len(dispatches) == 1


def test_batch_detail_includes_images_regenerating_flag(manager_client, db_session,
                                                         site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501, images_regenerating=True)
    db_session.add(article)
    db_session.commit()

    body = manager_client.get(f"/api/article-batches/{batch_id}").json()
    assert body["articles"][0]["images_regenerating"] is True
