from datetime import timedelta

import pytest

from app.clock import utcnow
from app.house_projects.runs import STALE_AFTER, STUCK_AFTER
from app.models.house_project import HouseProjectPage, ProjectRewriteRun
from app.models.site import Site
from app.sites.client import SiteAPIError


@pytest.fixture
def site(db_session):
    row = Site(name="Стройбаза Самара", domain="stroybaza-samara.ru",
               base_url="https://stroybaza-samara.ru", api_token_enc="e")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def project(db_session, site):
    site.projects_enabled, site.projects_parent_id = True, 15
    site.projects_parent_title = "Проекты домов"
    db_session.commit()
    return site


class FakeSite:
    def __init__(self, error=None):
        self.error = error

    def get_page(self, page_id):
        if self.error:
            raise self.error
        return {"id": page_id, "title": "Проекты домов", "parent": None}


@pytest.fixture
def site_client(monkeypatch):
    client = FakeSite()
    monkeypatch.setattr("app.api.house_projects.open_site_client", lambda db, site: client)
    return client


@pytest.fixture
def enqueued(monkeypatch):
    calls = {"runs": [], "pages": []}
    monkeypatch.setattr("app.api.house_projects.enqueue_rewrite_run",
                        lambda run_id: calls["runs"].append(run_id))
    monkeypatch.setattr("app.api.house_projects.enqueue_rewrite_page",
                        lambda page_id, **kw: calls["pages"].append((page_id, kw)))
    return calls


def add_page(db, site, remote_id, status="new", **fields):
    page = HouseProjectPage(site_id=site.id, remote_id=remote_id, status=status,
                            title=f"Проект {remote_id}", url=f"/pr/{remote_id}/", **fields)
    db.add(page)
    db.commit()
    return page


def test_requires_login(client):
    assert client.get("/api/house-projects/projects").status_code == 401


def test_create_project_checks_parent_and_shows_title(manager_client, site, site_client):
    resp = manager_client.post("/api/house-projects/projects",
                               json={"site_id": site.id, "parent_id": 15})
    assert resp.status_code == 200
    body = resp.json()
    assert (body["parent_id"], body["parent_title"], body["pages"], body["run"]) == \
        (15, "Проекты домов", 0, None)
    assert [p["site_id"] for p in manager_client.get("/api/house-projects/projects").json()] \
        == [site.id]


def test_create_project_unknown_parent(manager_client, site, site_client):
    site_client.error = SiteAPIError("страница 999: HTTP 404", status_code=404)
    resp = manager_client.post("/api/house-projects/projects",
                               json={"site_id": site.id, "parent_id": 999})
    assert (resp.status_code, resp.json()["detail"]) == (400, "страница 999 не найдена на сайте")
    assert not site.projects_enabled


def test_create_project_site_down(manager_client, site, site_client):
    site_client.error = SiteAPIError("сеть недоступна", status_code=None)
    resp = manager_client.post("/api/house-projects/projects",
                               json={"site_id": site.id, "parent_id": 15})
    assert resp.status_code == 502


def test_create_twice_and_unknown_site(manager_client, project, site_client):
    assert manager_client.post("/api/house-projects/projects",
                               json={"site_id": project.id, "parent_id": 15}).status_code == 400
    assert manager_client.post("/api/house-projects/projects",
                               json={"site_id": 999, "parent_id": 15}).status_code == 404


def test_project_counts(manager_client, db_session, project):
    now = utcnow()
    add_page(db_session, project, 16, "done", rewritten_at=now - timedelta(days=1))
    add_page(db_session, project, 17, "done", rewritten_at=now)
    add_page(db_session, project, 18, "new")
    add_page(db_session, project, 19, "failed", error_text="x")
    add_page(db_session, project, 20, "skipped", skip_reason="удалена на сайте")
    body = manager_client.get("/api/house-projects/projects").json()[0]
    assert (body["pages"], body["rewritten"], body["new_count"], body["error_count"],
            body["busy"]) == (4, 2, 2, 1, False)
    assert body["last_rewritten_at"].startswith(now.strftime("%Y-%m-%dT%H:%M"))


def test_run_starts_and_second_is_409(manager_client, db_session, project, enqueued):
    resp = manager_client.post(f"/api/house-projects/projects/{project.id}/run",
                               json={"mode": "all"})
    assert resp.status_code == 200
    run = db_session.query(ProjectRewriteRun).one()
    assert (run.mode, enqueued["runs"]) == ("all", [run.id])
    assert resp.json()["run"]["total"] == 0 and resp.json()["busy"]
    again = manager_client.post(f"/api/house-projects/projects/{project.id}/run",
                                json={"mode": "new"})
    assert again.status_code == 409


def test_run_rejects_unknown_mode(manager_client, project, enqueued):
    resp = manager_client.post(f"/api/house-projects/projects/{project.id}/run",
                               json={"mode": "everything"})
    assert resp.status_code == 422


def test_stale_run_is_aborted_and_restarted(manager_client, db_session, project, enqueued):
    long_ago = utcnow() - STALE_AFTER - timedelta(minutes=5)
    old = ProjectRewriteRun(site_id=project.id, total=1, started_at=long_ago)
    db_session.add(old)
    db_session.commit()
    page = add_page(db_session, project, 16, "queued", updated_at=long_ago)
    resp = manager_client.post(f"/api/house-projects/projects/{project.id}/run",
                               json={"mode": "new"})
    assert resp.status_code == 200
    assert old.finished_at is not None and old.error_text.startswith("прерван")
    assert page.status == "new"


def test_single_page_blocks_run(manager_client, db_session, project, enqueued):
    add_page(db_session, project, 16, "in_work", started_at=utcnow(), updated_at=utcnow())
    resp = manager_client.post(f"/api/house-projects/projects/{project.id}/run",
                               json={"mode": "new"})
    assert resp.status_code == 409


def test_list_and_detail_pages(manager_client, db_session, project):
    page = add_page(db_session, project, 16, "done", original_html="<p>Дом 82 м²</p>",
                    rewritten_html="<p>Дом на 82 м²</p>", rewritten_at=utcnow(), rewrite_count=2)
    rows = manager_client.get(f"/api/house-projects/projects/{project.id}/pages").json()
    assert rows[0]["page_url"] == "https://stroybaza-samara.ru/pr/16/"
    assert (rows[0]["original_chars"], rows[0]["rewritten_chars"], rows[0]["rewrite_count"]) == \
        (9, 12, 2)
    assert "original_html" not in rows[0]
    detail = manager_client.get(f"/api/house-projects/pages/{page.id}").json()
    assert detail["rewritten_html"] == "<p>Дом на 82 м²</p>"


def test_rewrite_one(manager_client, db_session, project, enqueued):
    page = add_page(db_session, project, 16, "done", error_text="")
    resp = manager_client.post(f"/api/house-projects/pages/{page.id}/rewrite")
    assert resp.status_code == 200
    assert page.status == "queued"
    assert enqueued["pages"] == [(page.id, {"continue_run": False})]
    assert manager_client.post(f"/api/house-projects/pages/{page.id}/rewrite").status_code == 409


def test_rewrite_one_refused_while_run_or_skipped(manager_client, db_session, project, enqueued):
    skipped = add_page(db_session, project, 16, "skipped", skip_reason="нет блока описания")
    resp = manager_client.post(f"/api/house-projects/pages/{skipped.id}/rewrite")
    assert (resp.status_code, resp.json()["detail"]) == \
        (400, "страница пропущена: нет блока описания")
    page = add_page(db_session, project, 17, "done")
    db_session.add(ProjectRewriteRun(site_id=project.id, started_at=utcnow()))
    db_session.commit()
    assert manager_client.post(f"/api/house-projects/pages/{page.id}/rewrite").status_code == 409


def test_rewrite_one_allowed_for_stuck_and_lost(manager_client, db_session, project, enqueued):
    stuck = add_page(db_session, project, 16, "in_work",
                     started_at=utcnow() - STUCK_AFTER - timedelta(minutes=1))
    lost = add_page(db_session, project, 17, "queued",
                    updated_at=utcnow() - STALE_AFTER - timedelta(minutes=1))
    rows = manager_client.get(f"/api/house-projects/projects/{project.id}/pages").json()
    assert [row["stuck"] for row in rows] == [True, True]
    assert manager_client.get("/api/house-projects/projects").json()[0]["error_count"] == 2
    for page in (stuck, lost):
        assert manager_client.post(f"/api/house-projects/pages/{page.id}/rewrite").status_code == 200


def test_rewrite_one_aborts_stale_run(manager_client, db_session, project, enqueued):
    long_ago = utcnow() - STALE_AFTER - timedelta(hours=1)
    run = ProjectRewriteRun(site_id=project.id, total=2, started_at=long_ago)
    db_session.add(run)
    db_session.commit()
    stuck = add_page(db_session, project, 16, "in_work", started_at=long_ago, updated_at=long_ago)
    waiting = add_page(db_session, project, 17, "queued", updated_at=long_ago)
    assert manager_client.post(f"/api/house-projects/pages/{stuck.id}/rewrite").status_code == 200
    assert run.finished_at is not None and run.error_text.startswith("прерван")
    assert (stuck.status, waiting.status) == ("queued", "new")
    body = manager_client.get("/api/house-projects/projects").json()[0]
    assert body["run"] is None and body["busy"]


def test_waiting_pages_of_live_run_are_not_lost(manager_client, db_session, project):
    now = utcnow()
    long_ago = now - STALE_AFTER - timedelta(hours=1)
    db_session.add(ProjectRewriteRun(site_id=project.id, total=2, started_at=long_ago))
    db_session.commit()
    add_page(db_session, project, 16, "done", rewritten_at=now, updated_at=now)
    add_page(db_session, project, 17, "queued", updated_at=long_ago)
    body = manager_client.get("/api/house-projects/projects").json()[0]
    assert (body["busy"], body["error_count"], body["run"]["stale"]) == (True, 0, False)
    rows = manager_client.get(f"/api/house-projects/projects/{project.id}/pages").json()
    assert [row["stuck"] for row in rows] == [False, False]


def test_update_and_delete_project(manager_client, db_session, project, site_client, enqueued):
    resp = manager_client.put(f"/api/house-projects/projects/{project.id}",
                              json={"site_id": project.id, "parent_id": 40})
    assert (resp.status_code, resp.json()["parent_id"]) == (200, 40)
    db_session.add(ProjectRewriteRun(site_id=project.id, started_at=utcnow()))
    db_session.commit()
    assert manager_client.delete(f"/api/house-projects/projects/{project.id}").status_code == 409
    run = db_session.query(ProjectRewriteRun).one()
    run.finished_at = utcnow()
    db_session.commit()
    assert manager_client.delete(f"/api/house-projects/projects/{project.id}").status_code == 200
    assert manager_client.get("/api/house-projects/projects").json() == []
