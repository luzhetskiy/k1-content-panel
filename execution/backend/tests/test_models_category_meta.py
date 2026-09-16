import pytest
from sqlalchemy.exc import IntegrityError

from app.models.category_meta import CategoryMeta, MetaRun, WordstatCache, WordstatCall
from app.models.site import Site


@pytest.fixture
def site(db_session):
    row = Site(name="Стройбаза", domain="s.ru", base_url="https://s.ru", api_token_enc="e")
    db_session.add(row)
    db_session.commit()
    return row


def test_site_meta_fields_default_empty(site):
    assert site.meta_enabled is False
    assert (site.city, site.city_in, site.brand) == ("", "", "")
    assert site.wordstat_region_id is None


def test_category_defaults(db_session, site):
    row = CategoryMeta(site_id=site.id, remote_id=46, name="Фанера")
    db_session.add(row)
    db_session.commit()
    assert row.status == "new"
    assert row.previous_json is None
    assert row.low_demand is False
    assert row.updated_at is not None


def test_category_unique_per_site(db_session, site):
    db_session.add_all([CategoryMeta(site_id=site.id, remote_id=46, name="А"),
                        CategoryMeta(site_id=site.id, remote_id=46, name="Б")])
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_previous_json_roundtrip(db_session, site):
    row = CategoryMeta(site_id=site.id, remote_id=1, name="А",
                       previous_json={"title": "Фанера", "h1": ""})
    db_session.add(row)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(CategoryMeta, row.id).previous_json == {"title": "Фанера", "h1": ""}


def test_candidates_json_roundtrip(db_session, site):
    variants = [{"phrase": "гипсокартон", "nominative": "гипсокартон", "buy": "купить гипсокартон",
                 "price": "цена гипсокартона", "count": 87575}]
    row = CategoryMeta(site_id=site.id, remote_id=2, name="ГКЛ", candidates_json=variants)
    db_session.add(row)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(CategoryMeta, row.id).candidates_json == variants
    assert CategoryMeta(site_id=site.id, remote_id=3, name="Б").candidates_json is None


def test_run_and_wordstat_rows(db_session, site):
    run = MetaRun(site_id=site.id)
    db_session.add_all([run, WordstatCache(kind="top", phrase="фанера", region_id=213,
                                           body={"totalCount": "1"}),
                        WordstatCall()])
    db_session.commit()
    assert run.finished_at is None and run.total == 0 and run.error_text == ""


def test_wordstat_cache_key_unique(db_session):
    db_session.add_all([WordstatCache(kind="top", phrase="фанера", region_id=213, body={}),
                        WordstatCache(kind="top", phrase="фанера", region_id=213, body={})])
    with pytest.raises(IntegrityError):
        db_session.commit()
