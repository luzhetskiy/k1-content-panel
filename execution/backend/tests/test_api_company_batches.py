import pytest

from app.models.company import CompanyCandidate
from app.models.site import Site


@pytest.fixture
def site_id(db_session):
    site = Site(name="С", domain="s.ru", base_url="https://s.ru", api_token_enc="e")
    db_session.add(site)
    db_session.commit()
    return site.id


@pytest.fixture
def candidates(db_session):
    db_session.add_all([
        CompanyCandidate(site_key="a.ru", name="А", region_raw="Самара",
                         category_raw="Дома", reviews_count=10),
        CompanyCandidate(site_key="b.ru", name="Б", region_raw="Самара",
                         category_raw="Дома", reviews_count=5),
    ])
    db_session.commit()


def test_create_batch_selects_top_candidates(manager_client, site_id, candidates):
    resp = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "selection_review"
    assert [c["name"] for c in body["companies"]] == ["А", "Б"]


def test_create_batch_also_creates_company_info(manager_client, site_id, candidates, db_session):
    resp = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    })
    body = resp.json()
    from app.models.company import Company
    company = db_session.get(Company, body["companies"][0]["id"])
    assert company.info is not None
    assert company.info.builder_name == "А"


def test_remove_company_from_batch(manager_client, site_id, candidates):
    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    company_id = batch["companies"][0]["id"]

    resp = manager_client.delete(f"/api/company-batches/{batch['id']}/companies/{company_id}")
    assert resp.status_code == 200
    assert len(resp.json()["companies"]) == 1


def test_add_next_after_removal(manager_client, site_id, db_session):
    db_session.add_all([
        CompanyCandidate(site_key="a.ru", name="А", region_raw="Самара",
                         category_raw="Дома", reviews_count=10),
        CompanyCandidate(site_key="b.ru", name="Б", region_raw="Самара",
                         category_raw="Дома", reviews_count=5),
        CompanyCandidate(site_key="c.ru", name="В", region_raw="Самара",
                         category_raw="Дома", reviews_count=1),
    ])
    db_session.commit()
    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    company_id = batch["companies"][1]["id"]   # «Б», reviews=5
    manager_client.delete(f"/api/company-batches/{batch['id']}/companies/{company_id}")

    resp = manager_client.post(f"/api/company-batches/{batch['id']}/companies/next")
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()["companies"]]
    assert names == ["А", "В"]   # добрали «В» (следующая по рейтингу после «Б»)


def test_add_next_returns_400_when_nothing_left(manager_client, site_id, candidates):
    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    resp = manager_client.post(f"/api/company-batches/{batch['id']}/companies/next")
    assert resp.status_code == 400


def test_unknown_site_rejected(manager_client, candidates):
    resp = manager_client.post("/api/company-batches", json={
        "site_id": 999, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    })
    assert resp.status_code == 404


def test_create_batch_rejected_for_deactivated_site(manager_client, db_session, candidates):
    site = Site(name="Неактивный", domain="inactive.ru", base_url="https://inactive.ru",
               api_token_enc="e", is_active=False)
    db_session.add(site)
    db_session.commit()

    resp = manager_client.post("/api/company-batches", json={
        "site_id": site.id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    })
    assert resp.status_code == 400


def test_remove_company_not_in_batch_returns_404(manager_client, site_id, db_session):
    db_session.add_all([
        CompanyCandidate(site_key="a.ru", name="А", region_raw="Самара",
                         category_raw="Дома", reviews_count=10),
        CompanyCandidate(site_key="b.ru", name="Б", region_raw="Казань",
                         category_raw="Дома", reviews_count=5),
    ])
    db_session.commit()

    batch_a = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 1,
    }).json()
    batch_b = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Казань", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 1,
    }).json()
    company_id_from_b = batch_b["companies"][0]["id"]

    # Компания вообще не существует.
    assert manager_client.delete(
        f"/api/company-batches/{batch_a['id']}/companies/999").status_code == 404
    # Компания существует, но принадлежит другой партии.
    resp = manager_client.delete(
        f"/api/company-batches/{batch_a['id']}/companies/{company_id_from_b}")
    assert resp.status_code == 404


def test_read_nonexistent_batch_returns_404(manager_client):
    resp = manager_client.get("/api/company-batches/999")
    assert resp.status_code == 404


def test_create_batch_requires_auth(client, site_id):
    resp = client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    })
    assert resp.status_code == 401


# --- /run, /retry (Task 14) ---

@pytest.fixture
def no_celery(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.api.company_batches.run_company_batch.apply_async",
        lambda args, **kwargs: sent.append(("run", args[0], kwargs)) or type("R", (), {"id": "t"})())
    monkeypatch.setattr(
        "app.api.company_batches.retry_company.apply_async",
        lambda args, **kwargs: sent.append(("retry", args[0], kwargs)) or type("R", (), {"id": "t"})())
    return sent


def test_run_dispatches_task_and_marks_running(manager_client, site_id, candidates, no_celery):
    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    resp = manager_client.post(f"/api/company-batches/{batch['id']}/run")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    assert len(no_celery) == 1
    assert no_celery[0][:2] == ("run", batch["id"])
    kwargs = no_celery[0][2]
    assert kwargs["soft_time_limit"] > 0
    assert kwargs["time_limit"] > kwargs["soft_time_limit"]


def test_run_twice_dispatches_once(manager_client, site_id, candidates, no_celery):
    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    manager_client.post(f"/api/company-batches/{batch['id']}/run")
    resp = manager_client.post(f"/api/company-batches/{batch['id']}/run")
    assert resp.status_code == 400
    assert len(no_celery) == 1


def test_run_rejects_empty_batch(manager_client, site_id):
    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    resp = manager_client.post(f"/api/company-batches/{batch['id']}/run")
    assert resp.status_code == 400


def test_retry_dispatches_task_and_marks_generating(manager_client, site_id, candidates, no_celery, db_session):
    from app.models.company import Company

    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    company_id = batch["companies"][0]["id"]
    company = db_session.get(Company, company_id)
    company.status = "failed"
    company.error_text = "старая ошибка"
    db_session.commit()

    resp = manager_client.post(f"/api/companies/{company_id}/retry")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert no_celery[-1][:2] == ("retry", company_id)

    db_session.refresh(company)
    assert company.status == "generating"


def test_retry_rejects_company_already_generating(manager_client, site_id, candidates, no_celery, db_session):
    from app.models.company import Company

    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    company_id = batch["companies"][0]["id"]
    company = db_session.get(Company, company_id)
    company.status = "generating"
    db_session.commit()

    resp = manager_client.post(f"/api/companies/{company_id}/retry")
    assert resp.status_code == 400
    assert no_celery == []


def test_retry_rejects_published_company(manager_client, site_id, candidates, no_celery, db_session):
    from app.models.company import Company

    batch = manager_client.post("/api/company-batches", json={
        "site_id": site_id, "region_raw": "Самара", "category_raw": "Дома",
        "category_normalized": "Дома под ключ", "teaser_category_id": 3,
        "teaser_city_id": 1, "teaser_location_id": 1, "count": 2,
    }).json()
    company_id = batch["companies"][0]["id"]
    company = db_session.get(Company, company_id)
    company.status = "published"
    db_session.commit()

    resp = manager_client.post(f"/api/companies/{company_id}/retry")
    assert resp.status_code == 400
    assert no_celery == []


def test_retry_rejects_unknown_company(manager_client, no_celery):
    resp = manager_client.post("/api/companies/999999/retry")
    assert resp.status_code == 404


def test_company_time_limits_scale_with_count():
    from app.api.company_batches import _company_time_limits
    soft1, hard1 = _company_time_limits(1)
    soft5, hard5 = _company_time_limits(5)
    assert soft5 > soft1
    assert hard5 > soft5 > 0
