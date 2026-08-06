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
