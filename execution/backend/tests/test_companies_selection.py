from app.companies.selection import add_next_candidate, select_candidates
from app.models.company import Company, CompanyCandidate


def _candidate(db, **over):
    defaults = dict(site_key="a.ru", name="А", region_raw="Самара",
                    category_raw="Дома", reviews_count=1)
    defaults.update(over)
    c = CompanyCandidate(**defaults)
    db.add(c)
    db.commit()
    return c


def test_select_filters_by_region_and_category(db_session):
    _candidate(db_session, site_key="a.ru", region_raw="Самара", category_raw="Дома")
    _candidate(db_session, site_key="b.ru", region_raw="Москва", category_raw="Дома")
    result = select_candidates(db_session, site_id=1, region_raw="Самара",
                               category_raw="Дома", count=10)
    assert [c.site_key for c in result] == ["a.ru"]


def test_select_sorts_by_reviews_desc(db_session):
    _candidate(db_session, site_key="a.ru", reviews_count=3)
    _candidate(db_session, site_key="b.ru", reviews_count=10)
    result = select_candidates(db_session, site_id=1, region_raw="Самара",
                               category_raw="Дома", count=10)
    assert [c.site_key for c in result] == ["b.ru", "a.ru"]


def test_select_respects_count(db_session):
    for i in range(5):
        _candidate(db_session, site_key=f"{i}.ru", reviews_count=i)
    result = select_candidates(db_session, site_id=1, region_raw="Самара",
                               category_raw="Дома", count=2)
    assert len(result) == 2


def test_select_excludes_candidates_already_taken_for_site(db_session):
    _candidate(db_session, site_key="a.ru")
    db_session.add(Company(site_id=1, site_key="a.ru", name="А"))
    db_session.commit()
    result = select_candidates(db_session, site_id=1, region_raw="Самара",
                               category_raw="Дома", count=10)
    assert result == []


def test_select_allows_same_candidate_on_different_site(db_session):
    _candidate(db_session, site_key="a.ru")
    db_session.add(Company(site_id=2, site_key="a.ru", name="А"))
    db_session.commit()
    result = select_candidates(db_session, site_id=1, region_raw="Самара",
                               category_raw="Дома", count=10)
    assert [c.site_key for c in result] == ["a.ru"]


def test_add_next_candidate_skips_already_in_batch(db_session):
    _candidate(db_session, site_key="a.ru", reviews_count=10)
    _candidate(db_session, site_key="b.ru", reviews_count=5)
    taken_site_keys = {"a.ru"}
    excluded_site_keys: set[str] = set()
    next_candidate = add_next_candidate(
        db_session, site_id=1, region_raw="Самара", category_raw="Дома",
        already_in_batch=taken_site_keys, excluded=excluded_site_keys)
    assert next_candidate.site_key == "b.ru"


def test_add_next_candidate_returns_none_when_exhausted(db_session):
    _candidate(db_session, site_key="a.ru")
    result = add_next_candidate(
        db_session, site_id=1, region_raw="Самара", category_raw="Дома",
        already_in_batch={"a.ru"}, excluded=set())
    assert result is None
