from datetime import timedelta

import pytest

from app.ai.factory import AIConfigError
from app.clock import utcnow
from app.models.category_meta import CategoryMeta, MetaRun
from app.models.job import JobRun
from app.models.site import Site
from app.wordstat.quota import QuotaExceeded
from app.wordstat.regions import Region


@pytest.fixture
def site(db_session):
    row = Site(name="Стройбаза Москва", domain="stroybaza-moscow.ru",
               base_url="https://stroybaza-moscow.ru", api_token_enc="e")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def project(db_session, site):
    site.meta_enabled, site.city, site.city_in = True, "Москва", "в Москве"
    site.brand, site.wordstat_region_id = "Стройбаза", 213
    db_session.commit()
    return site


@pytest.fixture
def enqueued(monkeypatch):
    calls = {"runs": [], "categories": []}
    monkeypatch.setattr("app.api.category_meta.enqueue_meta_run",
                        lambda run_id: calls["runs"].append(run_id))
    monkeypatch.setattr("app.api.category_meta.enqueue_category_meta",
                        lambda category_id, **kw: calls["categories"].append((category_id, kw)))
    return calls


def payload(site, **overrides):
    return {"site_id": site.id, "city": "Москва", "city_in": "в Москве", "brand": "Стройбаза",
            "wordstat_region_id": 213, **overrides}


def test_requires_login(client):
    assert client.get("/api/category-meta/projects").status_code == 401


def test_create_project_with_city_in(manager_client, site):
    resp = manager_client.post("/api/category-meta/projects", json=payload(site))
    assert resp.status_code == 200
    body = resp.json()
    assert (body["city_in"], body["brand"], body["updated_at"], body["run"]) == \
        ("в Москве", "Стройбаза", None, None)
    assert [p["site_id"] for p in manager_client.get("/api/category-meta/projects").json()] == [site.id]


def test_create_project_fills_city_in_by_llm(manager_client, site, monkeypatch):
    monkeypatch.setattr("app.api.category_meta.build_text_client", lambda db, max_retries=None: object())
    monkeypatch.setattr("app.api.category_meta.city_in_for", lambda client, city: "во Владимире")
    resp = manager_client.post("/api/category-meta/projects",
                               json=payload(site, city="Владимир", city_in="", wordstat_region_id=192))
    assert resp.json()["city_in"] == "во Владимире"


def test_create_project_without_routerai_asks_manual_city_in(manager_client, site, monkeypatch):
    def no_key(db, max_retries=None):
        raise AIConfigError("ключ RouterAI не задан")

    monkeypatch.setattr("app.api.category_meta.build_text_client", no_key)
    resp = manager_client.post("/api/category-meta/projects", json=payload(site, city_in=""))
    assert resp.status_code == 400
    assert "вручную" in resp.json()["detail"]


def test_create_project_validates(manager_client, site):
    assert manager_client.post("/api/category-meta/projects",
                               json=payload(site, brand=" ")).status_code == 400
    assert manager_client.post("/api/category-meta/projects",
                               json=payload(site, site_id=999)).status_code == 404
    manager_client.post("/api/category-meta/projects", json=payload(site))
    assert manager_client.post("/api/category-meta/projects",
                               json=payload(site)).status_code == 400


def test_update_project_city_change_recomputes_city_in(manager_client, project, monkeypatch):
    monkeypatch.setattr("app.api.category_meta.build_text_client", lambda db, max_retries=None: object())
    monkeypatch.setattr("app.api.category_meta.city_in_for", lambda client, city: "в Твери")
    resp = manager_client.put(f"/api/category-meta/projects/{project.id}",
                              json=payload(project, city="Тверь", wordstat_region_id=14))
    assert resp.json()["city_in"] == "в Твери"


def test_delete_project_keeps_categories(manager_client, project, db_session):
    db_session.add(CategoryMeta(site_id=project.id, remote_id=46, name="Фанера"))
    db_session.commit()
    assert manager_client.delete(f"/api/category-meta/projects/{project.id}").status_code == 200
    assert manager_client.get("/api/category-meta/projects").json() == []
    assert db_session.query(CategoryMeta).count() == 1


def test_regions_search(manager_client, monkeypatch):
    monkeypatch.setattr("app.api.category_meta.load_regions", lambda db, limit, factory: [
        Region(213, "Москва", "Россия / Москва"), Region(1, "Москва и область", "Россия / …")])
    body = manager_client.get("/api/category-meta/regions", params={"q": "москва"}).json()
    assert [r["id"] for r in body] == [213, 1]


def test_regions_quota_exhausted(manager_client, monkeypatch):
    def no_quota(db, limit, factory):
        raise QuotaExceeded(125)

    monkeypatch.setattr("app.api.category_meta.load_regions", no_quota)
    resp = manager_client.get("/api/category-meta/regions", params={"q": "москва"})
    assert resp.status_code == 429
    assert "через 3 мин" in resp.json()["detail"]


def test_regions_without_key(manager_client):
    resp = manager_client.get("/api/category-meta/regions", params={"q": "москва"})
    assert resp.status_code == 400
    assert "wordstat_api_key" in resp.json()["detail"]


def test_run_creates_run_and_enqueues(manager_client, project, enqueued, db_session):
    resp = manager_client.post(f"/api/category-meta/projects/{project.id}/run")
    assert resp.status_code == 200
    run = db_session.query(MetaRun).one()
    assert enqueued["runs"] == [run.id]
    assert resp.json()["run"]["id"] == run.id


def test_run_requires_complete_project(manager_client, project, enqueued, db_session):
    project.wordstat_region_id = None
    db_session.commit()
    assert manager_client.post(f"/api/category-meta/projects/{project.id}/run").status_code == 400


def test_second_run_while_active_is_conflict(manager_client, project, enqueued, db_session):
    manager_client.post(f"/api/category-meta/projects/{project.id}/run")
    assert manager_client.post(f"/api/category-meta/projects/{project.id}/run").status_code == 409
    assert len(enqueued["runs"]) == 1


def test_stale_run_can_be_restarted(manager_client, project, enqueued, db_session):
    job = JobRun(kind="category_meta_run", site_id=project.id)
    db_session.add(job)
    db_session.commit()
    old = MetaRun(site_id=project.id, job_run_id=job.id, started_at=utcnow() - timedelta(hours=5))
    db_session.add_all([old, CategoryMeta(site_id=project.id, remote_id=1, name="К", status="queued",
                                          updated_at=utcnow() - timedelta(hours=4))])
    db_session.commit()
    project_body = manager_client.get("/api/category-meta/projects").json()[0]
    assert project_body["run"]["stale"] is True
    assert manager_client.post(f"/api/category-meta/projects/{project.id}/run").status_code == 200
    db_session.refresh(old)
    assert old.finished_at is not None and old.error_text.startswith("прерван")
    db_session.refresh(job)
    assert job.status == "failed"
    assert len(enqueued["runs"]) == 1


def test_project_progress_and_last_update(manager_client, project, db_session):
    finished = utcnow() - timedelta(days=1)
    db_session.add_all([
        MetaRun(site_id=project.id, total=2, started_at=finished - timedelta(hours=2),
                finished_at=finished),
        MetaRun(site_id=project.id, total=3, started_at=utcnow() - timedelta(minutes=5)),
        CategoryMeta(site_id=project.id, remote_id=1, name="А", status="done"),
        CategoryMeta(site_id=project.id, remote_id=2, name="Б", status="queued",
                     wait_until=utcnow() + timedelta(minutes=20)),
        CategoryMeta(site_id=project.id, remote_id=3, name="В", status="failed"),
    ])
    db_session.commit()
    body = manager_client.get("/api/category-meta/projects").json()[0]
    assert body["updated_at"] is not None
    assert (body["run"]["total"], body["run"]["done"], body["run"]["failed"]) == (3, 1, 1)
    assert body["run"]["wait_until"] is not None and body["run"]["stale"] is False


def test_categories_list(manager_client, project, db_session):
    db_session.add_all([
        CategoryMeta(site_id=project.id, remote_id=46, remote_parent_id=45, name="Фанера",
                     path="Листовые материалы / Фанера",
                     url="/catalog/category/listovye-materialy/fanera/", status="done",
                     title="Фанера в Москве | Стройбаза", previous_json={"h1": "старый"},
                     candidates_json=[{"phrase": "фанера", "nominative": "фанера",
                                       "buy": "купить фанеру", "price": "цена фанеры",
                                       "count": 96275, "same_product": True}]),
        CategoryMeta(site_id=project.id, remote_id=45, name="Листовые материалы",
                     path="Листовые материалы", url="/catalog/listovye-materialy/",
                     status="in_work", started_at=utcnow() - timedelta(hours=1)),
    ])
    db_session.commit()
    body = manager_client.get(f"/api/category-meta/projects/{project.id}/categories").json()
    assert [c["remote_id"] for c in body] == [45, 46]
    assert body[0]["stuck"] is True
    assert body[1]["page_url"] == \
        "https://stroybaza-moscow.ru/catalog/category/listovye-materialy/fanera/"
    assert body[1]["previous_json"] == {"h1": "старый"}
    assert body[1]["candidates"] == [{"phrase": "фанера", "count": 96275, "same_product": True}]
    assert body[0]["candidates"] == []


def category(db, project, **kwargs):
    row = CategoryMeta(site_id=project.id, remote_id=46, name="Фанера", seed_phrase="фанера",
                       **kwargs)
    db.add(row)
    db.commit()
    return row


def test_regenerate_enqueues_single_category(manager_client, project, enqueued, db_session):
    row = category(db_session, project, status="done")
    resp = manager_client.post(f"/api/category-meta/categories/{row.id}/regenerate")
    assert resp.status_code == 200 and resp.json()["status"] == "queued"
    assert enqueued["categories"] == [(row.id, {"continue_run": False})]


@pytest.mark.parametrize("status,started_hours_ago,code", [
    ("queued", None, 409), ("in_work", 0, 409), ("in_work", 2, 200), ("failed", None, 200),
    ("skipped", None, 400),
])
def test_regenerate_rules(manager_client, project, enqueued, db_session, status,
                          started_hours_ago, code):
    extra = {}
    if started_hours_ago is not None:
        extra["started_at"] = utcnow() - timedelta(hours=started_hours_ago)
    row = category(db_session, project, status=status, **extra)
    assert manager_client.post(f"/api/category-meta/categories/{row.id}/regenerate").status_code == code


def test_regenerate_without_seed(manager_client, project, enqueued, db_session):
    row = CategoryMeta(site_id=project.id, remote_id=46, name="Фанера", status="new")
    db_session.add(row)
    db_session.commit()
    assert manager_client.post(f"/api/category-meta/categories/{row.id}/regenerate").status_code == 400


def test_project_counts_failed_and_stuck_categories(manager_client, db_session, project):
    long_ago = utcnow() - timedelta(hours=2)
    db_session.add_all([
        CategoryMeta(site_id=project.id, remote_id=1, name="А", status="failed"),
        CategoryMeta(site_id=project.id, remote_id=2, name="Б", status="in_work",
                     started_at=long_ago),
        CategoryMeta(site_id=project.id, remote_id=3, name="В", status="in_work",
                     started_at=utcnow()),
        CategoryMeta(site_id=project.id, remote_id=4, name="Г", status="done"),
        CategoryMeta(site_id=project.id, remote_id=5, name="Д", status="skipped"),
    ])
    db_session.commit()
    [body] = manager_client.get("/api/category-meta/projects").json()
    assert body["error_count"] == 2


def test_project_without_errors_has_zero_error_count(manager_client, db_session, project):
    db_session.add(CategoryMeta(site_id=project.id, remote_id=1, name="А", status="done"))
    db_session.commit()
    [body] = manager_client.get("/api/category-meta/projects").json()
    assert body["error_count"] == 0


def retry_rows(db, project):
    long_ago = utcnow() - timedelta(hours=2)
    rows = [
        CategoryMeta(site_id=project.id, remote_id=1, name="А", seed_phrase="а", status="failed",
                     error_text="не ответил", updated_at=long_ago),
        CategoryMeta(site_id=project.id, remote_id=2, name="Б", seed_phrase="б", status="in_work",
                     started_at=long_ago, updated_at=long_ago),
        CategoryMeta(site_id=project.id, remote_id=3, name="В", status="failed",
                     error_text="нет фразы", updated_at=long_ago),
        CategoryMeta(site_id=project.id, remote_id=4, name="Г", seed_phrase="г", status="done",
                     updated_at=long_ago),
    ]
    db.add_all(rows)
    db.commit()
    return rows


def test_retry_errors_queues_only_failed_and_stuck(manager_client, project, enqueued, db_session):
    failed, stuck, seedless, done = retry_rows(db_session, project)
    before = manager_client.get("/api/category-meta/projects").json()[0]
    assert (before["error_count"], before["retry_count"]) == (3, 2)
    resp = manager_client.post(f"/api/category-meta/projects/{project.id}/retry-errors")
    assert resp.status_code == 200
    for row in (failed, stuck, seedless, done):
        db_session.refresh(row)
    assert (failed.status, stuck.status) == ("queued", "queued")
    assert failed.error_text == "" and stuck.started_at is None
    assert (seedless.status, done.status) == ("failed", "done")
    run = db_session.query(MetaRun).one()
    assert run.total == 2 and run.job_run_id is not None
    assert enqueued["categories"] == [(failed.id, {})]
    assert enqueued["runs"] == []
    body = resp.json()
    assert (body["run"]["total"], body["run"]["done"], body["run"]["failed"]) == (2, 0, 0)


def test_retry_errors_progress_ignores_old_errors(manager_client, project, enqueued, db_session):
    failed, stuck, _seedless, _done = retry_rows(db_session, project)
    manager_client.post(f"/api/category-meta/projects/{project.id}/retry-errors")
    failed.status = "done"
    stuck.status, stuck.error_text, stuck.updated_at = "failed", "снова", utcnow()
    db_session.commit()
    body = manager_client.get("/api/category-meta/projects").json()[0]
    assert (body["run"]["done"], body["run"]["failed"]) == (1, 1)


def test_retry_errors_nothing_to_retry(manager_client, project, enqueued, db_session):
    db_session.add(CategoryMeta(site_id=project.id, remote_id=3, name="В", status="failed"))
    db_session.commit()
    resp = manager_client.post(f"/api/category-meta/projects/{project.id}/retry-errors")
    assert resp.status_code == 400
    assert db_session.query(MetaRun).count() == 0


def test_retry_errors_while_running_is_conflict(manager_client, project, enqueued, db_session):
    retry_rows(db_session, project)
    db_session.add(MetaRun(site_id=project.id, total=5))
    db_session.commit()
    resp = manager_client.post(f"/api/category-meta/projects/{project.id}/retry-errors")
    assert resp.status_code == 409
    assert enqueued["categories"] == []


def test_retry_all_errors(manager_client, project, enqueued, db_session):
    failed, *_ = retry_rows(db_session, project)
    busy = Site(name="Стройбаза Калуга", domain="k.ru", base_url="https://k.ru",
                api_token_enc="e", meta_enabled=True, city="Калуга", city_in="в Калуге",
                brand="Стройбаза", wordstat_region_id=6)
    clean = Site(name="Стройбаза Брянск", domain="b.ru", base_url="https://b.ru",
                 api_token_enc="e", meta_enabled=True, city="Брянск", city_in="в Брянске",
                 brand="Стройбаза", wordstat_region_id=191)
    db_session.add_all([busy, clean])
    db_session.commit()
    db_session.add_all([
        CategoryMeta(site_id=busy.id, remote_id=1, name="А", seed_phrase="а", status="failed"),
        MetaRun(site_id=busy.id, total=3),
        CategoryMeta(site_id=clean.id, remote_id=1, name="А", seed_phrase="а", status="done"),
    ])
    db_session.commit()
    resp = manager_client.post("/api/category-meta/retry-errors")
    assert resp.status_code == 200
    assert resp.json() == {"sites": 1, "categories": 2, "busy": ["Стройбаза Калуга"]}
    assert enqueued["categories"] == [(failed.id, {})]
    assert db_session.query(MetaRun).filter(MetaRun.site_id == clean.id).count() == 0
