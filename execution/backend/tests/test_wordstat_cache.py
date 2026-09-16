from datetime import datetime, timedelta, timezone

from app.wordstat.cache import cache_get, cache_put

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def test_miss_then_hit(db_session):
    assert cache_get(db_session, "top", "фанера", 213, NOW) is None
    cache_put(db_session, "top", "фанера", 213, {"totalCount": "5"}, NOW)
    assert cache_get(db_session, "top", "фанера", 213, NOW) == {"totalCount": "5"}


def test_key_includes_kind_and_region(db_session):
    cache_put(db_session, "top", "фанера", 213, {"a": 1}, NOW)
    assert cache_get(db_session, "exact", "фанера", 213, NOW) is None
    assert cache_get(db_session, "top", "фанера", 2, NOW) is None


def test_expired_after_30_days(db_session):
    cache_put(db_session, "top", "фанера", 213, {"a": 1}, NOW - timedelta(days=31))
    assert cache_get(db_session, "top", "фанера", 213, NOW) is None
    assert cache_get(db_session, "top", "фанера", 213, NOW - timedelta(days=2)) == {"a": 1}


def test_put_overwrites(db_session):
    cache_put(db_session, "top", "фанера", 213, {"a": 1}, NOW - timedelta(days=31))
    cache_put(db_session, "top", "фанера", 213, {"a": 2}, NOW)
    assert cache_get(db_session, "top", "фанера", 213, NOW) == {"a": 2}


def test_none_region_is_zero(db_session):
    cache_put(db_session, "regions", "", None, {"regions": []}, NOW)
    assert cache_get(db_session, "regions", "", 0, NOW) == {"regions": []}
