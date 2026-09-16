import pytest
from sqlalchemy import func, select

from app.models.category_meta import WordstatCall
from app.wordstat.cache import cache_put
from app.wordstat.quota import QuotaExceeded
from app.wordstat.regions import Region, flatten_regions, load_regions, search_regions

TREE = {"regions": [{"id": "225", "label": "Россия", "children": [
    {"id": "1", "label": "Москва и Московская область", "children": [
        {"id": "213", "label": "Москва", "children": [{"id": "216", "label": "Зеленоград"}]},
        {"id": "10716", "label": "Балашиха"},
    ]},
    {"id": "192", "label": "Владимир"},
    {"id": "bad", "label": "Без id"},
]}]}


def test_flatten_builds_paths():
    regions = flatten_regions(TREE)
    moscow = next(r for r in regions if r.id == 213)
    assert moscow.path == "Россия / Москва и Московская область / Москва"
    assert Region(216, "Зеленоград",
                  "Россия / Москва и Московская область / Москва / Зеленоград") in regions
    assert all(r.label != "Без id" for r in regions)


def test_search_exact_first_then_prefix():
    found = search_regions(flatten_regions(TREE), " москва ")
    assert [r.id for r in found] == [213, 1]


def test_search_empty_query():
    assert search_regions(flatten_regions(TREE), "  ") == []


class FakeWordstat:
    def __init__(self):
        self.calls = 0

    def regions_tree(self):
        self.calls += 1
        return TREE


def test_load_regions_fetches_once_and_caches(db_session):
    fake = FakeWordstat()
    assert len(load_regions(db_session, 100, lambda: fake)) == 6
    assert len(load_regions(db_session, 100, lambda: fake)) == 6
    assert fake.calls == 1
    assert db_session.scalar(select(func.count()).select_from(WordstatCall)) == 1


def test_load_regions_from_cache_does_not_build_client(db_session):
    cache_put(db_session, "regions", "", 0, TREE)

    def no_client():
        raise AssertionError("клиент не нужен — дерево в кеше")

    assert len(load_regions(db_session, 100, no_client)) == 6


def test_load_regions_quota_exhausted(db_session):
    fake = FakeWordstat()
    load_regions_limit = 1
    db_session.add(WordstatCall())
    db_session.commit()
    with pytest.raises(QuotaExceeded):
        load_regions(db_session, load_regions_limit, lambda: fake)
    assert fake.calls == 0
