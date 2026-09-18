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
        "app.api.article_batches.regenerate_article.apply_async",
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


def test_regen_time_limits_soft_grows_with_each_part():
    from app.api.article_batches import _regen_time_limits

    none_selected, _ = _regen_time_limits(text=False, image_count=0, cover=False)
    text_only, _ = _regen_time_limits(text=True, image_count=0, cover=False)
    images_only, _ = _regen_time_limits(text=False, image_count=1, cover=False)
    cover_only, _ = _regen_time_limits(text=False, image_count=0, cover=True)
    all_parts, _ = _regen_time_limits(text=True, image_count=1, cover=True)

    assert text_only > none_selected
    assert images_only > none_selected
    assert cover_only > none_selected
    assert all_parts > max(text_only, images_only, cover_only)


def test_regen_time_limits_image_count_scales_soft_limit():
    from app.api.article_batches import _regen_time_limits

    soft_one, _ = _regen_time_limits(text=False, image_count=1, cover=False)
    soft_many, _ = _regen_time_limits(text=False, image_count=5, cover=False)
    assert soft_many > soft_one


def test_regen_time_limits_hard_is_always_after_soft():
    from app.api.article_batches import _regen_time_limits

    soft, hard = _regen_time_limits(text=True, image_count=3, cover=True)
    assert hard > soft


def test_regenerate_unknown_article_404(manager_client, no_celery):
    resp = manager_client.post("/api/articles/999/regenerate", json={"images": True})
    assert resp.status_code == 404


def test_regenerate_requires_at_least_one_part(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501)
    db_session.add(article)
    db_session.commit()

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate",
                               json={"text": False, "images": False, "cover": False})
    assert resp.status_code == 400


def test_regenerate_requires_published_article(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема", status="draft")
    db_session.add(article)
    db_session.commit()

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate", json={"images": True})
    assert resp.status_code == 400


def test_regenerate_starts_task_for_published_article(manager_client, db_session,
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

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate", json={"images": True})

    assert resp.status_code == 200
    assert any(entry[:2] == ("regenerate", article.id) for entry in no_celery)
    db_session.refresh(article)
    assert article.regenerating is True


def test_regenerate_passes_selected_parts_to_task(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501)
    db_session.add(article)
    db_session.commit()

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate",
                               json={"text": True, "images": False, "cover": True})

    assert resp.status_code == 200
    dispatch = next(entry for entry in no_celery if entry[0] == "regenerate")
    assert dispatch[2]["kwargs"] == {"text": True, "images": False, "cover": True}


def test_regenerate_time_limit_counts_distinct_positions_only_when_images_selected(
        manager_client, db_session, site_id, no_celery):
    """Регенерация не удаляет старые ArticleImage — вторая версия той же
    позиции добавляет новую строку, не заменяет старую (app/articles/
    builder.py, regenerate_content_images). Бюджет времени обязан считать
    иллюстрации (уникальные position), а не строки."""
    from app.api.article_batches import _regen_time_limits
    from app.models.article import Article, ArticleImage

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501)
    db_session.add(article)
    db_session.commit()
    db_session.add_all([
        ArticleImage(article_id=article.id, kind="content", position=1, version=1,
                    remote_path="/media/x/cp-article-1-1.webp"),
        ArticleImage(article_id=article.id, kind="content", position=1, version=2,
                    remote_path="/media/x/cp-article-1-1_v2.webp"),
    ])
    db_session.commit()

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate", json={"images": True})

    assert resp.status_code == 200
    dispatch = next(entry for entry in no_celery if entry[0] == "regenerate")
    soft_one, _ = _regen_time_limits(text=False, image_count=1, cover=False)
    assert dispatch[2]["soft_time_limit"] == soft_one


def test_regenerate_twice_dispatches_once(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501)
    db_session.add(article)
    db_session.commit()

    first = manager_client.post(f"/api/articles/{article.id}/regenerate", json={"images": True})
    second = manager_client.post(f"/api/articles/{article.id}/regenerate", json={"images": True})

    assert first.status_code == 200
    assert second.status_code == 400
    dispatches = [entry for entry in no_celery if entry[0] == "regenerate"]
    assert len(dispatches) == 1


def test_batch_detail_includes_regenerating_flag(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501, regenerating=True)
    db_session.add(article)
    db_session.commit()

    body = manager_client.get(f"/api/article-batches/{batch_id}").json()
    assert body["articles"][0]["regenerating"] is True


# --- batch_runtime_state: отличить «работает» от «зависла» (дизайн 2026-09-12 §6.1) ---
#
# Партия 25 девять дней показывала статус running без признаков того, что
# задача давно мертва: статус в БД — обещание, которое исправляет только сама
# задача, а она может умереть. Эти правила дают ответ на чтении, не полагаясь
# на то, что кто-то закрыл журнал.

def _batch(db_session, site_id, *, status="running", articles=1, run_ago_minutes=1):
    from datetime import timedelta

    from app.clock import utcnow
    from app.models.article import Article, ArticleBatch

    batch = ArticleBatch(site_id=site_id, requested_count=articles, created_by_id=1,
                         status=status,
                         run_requested_at=utcnow() - timedelta(minutes=run_ago_minutes))
    db_session.add(batch)
    db_session.commit()
    for i in range(articles):
        db_session.add(Article(batch_id=batch.id, site_id=site_id, topic=f"Тема {i}"))
    db_session.commit()
    return batch


def _job(db_session, batch, *, status="running", started_ago_seconds=60):
    """Джоба сборки партии.

    Заодно двигает run_requested_at партии на полминуты РАНЬШЕ старта джобы —
    это не косметика, а воспроизведение настоящей последовательности: сначала
    run() записывает запрос на запуск, и только потом воркер, взяв задачу,
    создаёт JobRun. Без этого хелпер делал оба момента ровесниками, и проверка
    «джоба от предыдущей попытки» (batch_runtime_state) на равных значениях
    давала результат, зависящий от порядка микросекунд.
    """
    from datetime import timedelta

    from app.clock import utcnow
    from app.models.job import JobRun

    started_at = utcnow() - timedelta(seconds=started_ago_seconds)
    job = JobRun(kind="run_batch", site_id=batch.site_id,
                 params_json={"batch_id": batch.id, "articles": len(batch.articles)},
                 status=status, started_at=started_at)
    db_session.add(job)
    batch.run_requested_at = started_at - timedelta(seconds=30)
    db_session.commit()
    return job


def test_runtime_state_is_none_for_not_running_batch(db_session, site_id):
    from app.api.article_batches import batch_runtime_state

    batch = _batch(db_session, site_id, status="done")
    assert batch_runtime_state(db_session, batch) is None


def test_runtime_state_queued_while_no_job_yet(db_session, site_id):
    """Задача поставлена, но воркеры заняты — JobRun появляется только в момент
    реального старта. Это не зависание, воркеров всего два."""
    from app.api.article_batches import batch_runtime_state

    batch = _batch(db_session, site_id, run_ago_minutes=2)
    assert batch_runtime_state(db_session, batch) == "queued"


def test_runtime_state_stuck_when_job_never_started(db_session, site_id):
    """Если джобы нет спустя час — задача до воркера не дошла (брокер не принял
    постановку), и партия висит в running без всякой задачи."""
    from app.api.article_batches import batch_runtime_state

    batch = _batch(db_session, site_id, run_ago_minutes=60)
    assert batch_runtime_state(db_session, batch) == "stuck"


def test_runtime_state_working_within_time_limit(db_session, site_id):
    from app.api.article_batches import batch_runtime_state

    batch = _batch(db_session, site_id, articles=3)
    _job(db_session, batch, started_ago_seconds=120)
    assert batch_runtime_state(db_session, batch) == "working"


def test_runtime_state_stuck_when_job_outlived_its_limit(db_session, site_id):
    """Граница — тот же жёсткий лимит, что выставлялся при постановке
    (_batch_time_limits), а не отдельное число: двух источников правды о
    лимите быть не должно."""
    from app.api.article_batches import _batch_time_limits, batch_runtime_state

    batch = _batch(db_session, site_id, articles=1)
    _, hard = _batch_time_limits(1)
    _job(db_session, batch, started_ago_seconds=hard + 60)
    assert batch_runtime_state(db_session, batch) == "stuck"


def test_runtime_state_stuck_when_job_finished_but_batch_still_running(db_session, site_id):
    """Это состояние реально есть в проде: партия 10 — done, а её джоба 29
    осталась running (задача умерла между двумя коммитами). Обратный случай —
    джоба закрыта, а партия всё ещё running — так же противоречив и означает,
    что статус партии уже никто не исправит."""
    from app.api.article_batches import batch_runtime_state

    batch = _batch(db_session, site_id)
    _job(db_session, batch, status="failed", started_ago_seconds=120)
    assert batch_runtime_state(db_session, batch) == "stuck"


def test_runtime_state_uses_latest_job_of_the_batch(db_session, site_id):
    """У партии с повторными запусками джоб несколько — решает последняя."""
    from app.api.article_batches import batch_runtime_state

    batch = _batch(db_session, site_id)
    _job(db_session, batch, status="failed", started_ago_seconds=9000)
    _job(db_session, batch, status="running", started_ago_seconds=60)
    assert batch_runtime_state(db_session, batch) == "working"


def test_runtime_state_ignores_jobs_of_other_batches(db_session, site_id):
    from app.api.article_batches import batch_runtime_state

    other = _batch(db_session, site_id, status="done")
    batch = _batch(db_session, site_id, run_ago_minutes=60)
    _job(db_session, other, status="running", started_ago_seconds=60)
    assert batch_runtime_state(db_session, batch) == "stuck"


def test_batch_out_exposes_runtime_state(admin_client, db_session, site_id):
    batch = _batch(db_session, site_id)
    _job(db_session, batch, started_ago_seconds=60)
    body = admin_client.get(f"/api/article-batches/{batch.id}").json()
    assert body["runtime_state"] == "working"
    assert body["run_requested_at"] is not None


# --- запуск и перезапуск (дизайн 2026-09-12 §6.2) ---

def test_run_twice_dispatches_once(manager_client, db_session, site_id, no_celery):
    """Защита от двойного клика, на которую ссылается комментарий в run(): до
    2026-09-13 теста на неё для статей не было (он существовал только у
    строителей), то есть правка этого эндпоинта могла её молча снять."""
    batch = _batch(db_session, site_id, status="topics_review")
    assert manager_client.post(f"/api/article-batches/{batch.id}/run").status_code == 200
    second = manager_client.post(f"/api/article-batches/{batch.id}/run")
    assert second.status_code == 400
    assert len([s for s in no_celery if s[0] == "run"]) == 1


def test_run_records_run_requested_at(manager_client, db_session, site_id, no_celery):
    from app.models.article import ArticleBatch

    batch = _batch(db_session, site_id, status="topics_review")
    db_session.query(ArticleBatch).filter_by(id=batch.id).update({"run_requested_at": None})
    db_session.commit()

    manager_client.post(f"/api/article-batches/{batch.id}/run")
    db_session.expire_all()
    assert db_session.get(ArticleBatch, batch.id).run_requested_at is not None


def test_run_rejects_working_batch(manager_client, db_session, site_id, no_celery):
    batch = _batch(db_session, site_id, articles=3)
    _job(db_session, batch, started_ago_seconds=120)
    response = manager_client.post(f"/api/article-batches/{batch.id}/run")
    assert response.status_code == 400
    assert "уже выполняется" in response.json()["detail"]
    assert not [s for s in no_celery if s[0] == "run"]


def test_run_rejects_queued_batch_with_its_own_message(
        manager_client, db_session, site_id, no_celery):
    """Отдельный текст, а не «уже выполняется»: менеджеру важно понимать, что
    партия не сломана, а ждёт свободный воркер — их всего два."""
    batch = _batch(db_session, site_id, run_ago_minutes=2)
    response = manager_client.post(f"/api/article-batches/{batch.id}/run")
    assert response.status_code == 400
    assert "ждёт свободный воркер" in response.json()["detail"]


def test_run_restarts_stuck_batch_and_clears_its_state(
        manager_client, db_session, site_id, no_celery):
    """Главное: партию, зависшую в running, до 2026-09-13 нельзя было
    перезапустить ни из интерфейса, ни через API — только правкой БД. Ровно это
    и случилось с партией 25."""
    from app.models.article import Article, ArticleBatch
    from app.models.job import JobRun

    batch = _batch(db_session, site_id, articles=2)
    stale_job = _job(db_session, batch, status="running",
                     started_ago_seconds=_batch_hard_limit(2) + 600)
    articles = sorted(batch.articles, key=lambda a: a.id)
    articles[0].status = "published"
    articles[1].status = "generating"
    db_session.commit()

    response = manager_client.post(f"/api/article-batches/{batch.id}/run")
    assert response.status_code == 200
    assert len([s for s in no_celery if s[0] == "run"]) == 1

    db_session.expire_all()
    # Недосчитанная статья переведена в failed — иначе она осталась бы
    # «Генерируется» навсегда, а её кнопка повтора недоступна в этом статусе.
    assert db_session.get(Article, articles[1].id).status == "failed"
    assert "оборвалась" in db_session.get(Article, articles[1].id).error_text
    # Опубликованную не трогаем: она уже есть на сайте, её пропустит сборка.
    assert db_session.get(Article, articles[0].id).status == "published"
    # Зависшая джоба закрыта, иначе она вечно висит в «Выполняется сейчас».
    closed = db_session.get(JobRun, stale_job.id)
    assert closed.status == "failed"
    assert closed.finished_at is not None
    assert db_session.get(ArticleBatch, batch.id).status == "running"


def _batch_hard_limit(article_count: int) -> int:
    from app.api.article_batches import _batch_time_limits

    return _batch_time_limits(article_count)[1]


def test_runtime_state_after_restart_is_queued_not_stuck(db_session, site_id):
    """Найдено прогоном на живом стенде: сразу после перезапуска зависшей партии
    последняя её джоба — ЗАКРЫТАЯ джоба прошлой попытки, и правило «джоба
    завершена, а партия running» объявляло партию зависшей снова. В проде окно
    между перезапуском и реальным стартом задачи — минуты (воркеров два), и всё
    это время интерфейс предлагал бы «Дособрать партию» ещё раз, то есть звал
    бы поставить вторую задачу на ту же партию и оплатить её дважды."""
    from datetime import timedelta

    from app.api.article_batches import batch_runtime_state
    from app.clock import utcnow
    from app.models.article import ArticleBatch

    batch = _batch(db_session, site_id, run_ago_minutes=120)
    _job(db_session, batch, status="failed", started_ago_seconds=9000)
    # Перезапуск: run() пишет свежий run_requested_at, джоба ещё не появилась.
    db_session.query(ArticleBatch).filter_by(id=batch.id).update(
        {"run_requested_at": utcnow() - timedelta(seconds=30)})
    db_session.commit()
    db_session.expire_all()

    batch = db_session.get(ArticleBatch, batch.id)
    assert batch_runtime_state(db_session, batch) == "queued"


def test_runtime_state_stuck_when_old_job_and_no_restart(db_session, site_id):
    """Обратная сторона того же правила: если перезапуска НЕ было, закрытая
    джоба при running-партии по-прежнему означает зависание."""
    from app.api.article_batches import batch_runtime_state

    batch = _batch(db_session, site_id, run_ago_minutes=180)
    _job(db_session, batch, status="failed", started_ago_seconds=9000)
    assert batch_runtime_state(db_session, batch) == "stuck"


# --- пауза партии (2026-09-18) ---

def test_pause_requests_stop_of_working_batch(manager_client, db_session, site_id, no_celery):
    from app.models.article import ArticleBatch

    batch = _batch(db_session, site_id, articles=3)
    _job(db_session, batch, started_ago_seconds=120)
    response = manager_client.post(f"/api/article-batches/{batch.id}/pause")
    assert response.status_code == 200
    body = response.json()
    # Статус остаётся running: задача ещё доделывает текущую статью.
    assert (body["status"], body["runtime_state"]) == ("running", "pausing")
    db_session.expire_all()
    assert db_session.get(ArticleBatch, batch.id).pause_requested_at is not None


def test_pause_queued_batch_also_waits_for_task(manager_client, db_session, site_id, no_celery):
    """Сразу в paused нельзя: задача уже в очереди, и после «Дособрать» их
    стало бы две на одну партию."""
    batch = _batch(db_session, site_id, run_ago_minutes=2)
    body = manager_client.post(f"/api/article-batches/{batch.id}/pause").json()
    assert (body["status"], body["runtime_state"]) == ("running", "pausing")


def test_pause_twice_is_rejected(manager_client, db_session, site_id, no_celery):
    batch = _batch(db_session, site_id, articles=2)
    _job(db_session, batch, started_ago_seconds=120)
    assert manager_client.post(f"/api/article-batches/{batch.id}/pause").status_code == 200
    response = manager_client.post(f"/api/article-batches/{batch.id}/pause")
    assert response.status_code == 400
    assert "уже" in response.json()["detail"]


def test_pause_rejects_batch_that_is_not_running(manager_client, db_session, site_id, no_celery):
    batch = _batch(db_session, site_id, status="done")
    response = manager_client.post(f"/api/article-batches/{batch.id}/pause")
    assert response.status_code == 400


def test_pause_stuck_batch_pauses_at_once(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch = _batch(db_session, site_id, articles=2)
    _job(db_session, batch, status="running", started_ago_seconds=_batch_hard_limit(2) + 600)
    article = sorted(batch.articles, key=lambda a: a.id)[1]
    article.status = "generating"
    db_session.commit()

    body = manager_client.post(f"/api/article-batches/{batch.id}/pause").json()
    assert (body["status"], body["runtime_state"]) == ("paused", None)
    db_session.expire_all()
    assert db_session.get(Article, article.id).status == "failed"


def test_run_rejects_pausing_batch(manager_client, db_session, site_id, no_celery):
    batch = _batch(db_session, site_id, articles=2)
    _job(db_session, batch, started_ago_seconds=120)
    manager_client.post(f"/api/article-batches/{batch.id}/pause")
    response = manager_client.post(f"/api/article-batches/{batch.id}/run")
    assert response.status_code == 400
    assert "останавливается" in response.json()["detail"]
    assert not [s for s in no_celery if s[0] == "run"]


def test_paused_batch_resumes_via_run(manager_client, db_session, site_id, no_celery):
    from app.clock import utcnow
    from app.models.article import ArticleBatch

    batch = _batch(db_session, site_id, status="paused", articles=2)
    batch.pause_requested_at = utcnow()
    db_session.commit()
    response = manager_client.post(f"/api/article-batches/{batch.id}/run")
    assert response.status_code == 200
    assert len([s for s in no_celery if s[0] == "run"]) == 1
    db_session.expire_all()
    resumed = db_session.get(ArticleBatch, batch.id)
    assert resumed.status == "running"
    assert resumed.pause_requested_at is None


def test_topics_of_paused_batch_without_published_can_be_edited(
        manager_client, db_session, site_id, no_celery):
    batch = _batch(db_session, site_id, status="paused", articles=2)
    response = manager_client.put(f"/api/article-batches/{batch.id}/topics",
                                  json={"topics": ["Новая тема"]})
    assert response.status_code == 200
    assert response.json()["status"] == "topics_review"
