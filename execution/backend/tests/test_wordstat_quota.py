from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models.category_meta import WordstatCall
from app.wordstat.quota import QuotaExceeded, reserve

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def calls_count(db):
    return db.scalar(select(func.count()).select_from(WordstatCall))


def add_calls(db, count, minutes_ago):
    db.add_all([WordstatCall(called_at=NOW - timedelta(minutes=minutes_ago)) for _ in range(count)])
    db.commit()


def test_reserve_under_limit_records_calls(db_session):
    assert reserve(db_session, 3, 100, NOW) == 0.0
    assert calls_count(db_session) == 3


def test_reserve_zero_does_nothing(db_session):
    assert reserve(db_session, 0, 100, NOW) == 0.0
    assert calls_count(db_session) == 0


def test_reserve_exactly_to_limit(db_session):
    add_calls(db_session, 97, minutes_ago=10)
    assert reserve(db_session, 3, 100, NOW) == 0.0
    assert calls_count(db_session) == 100


def test_over_limit_returns_wait_until_oldest_needed_slot_frees(db_session):
    add_calls(db_session, 1, minutes_ago=50)
    add_calls(db_session, 99, minutes_ago=40)
    # Нужен 1 слот: освободится, когда запросу 50-минутной давности исполнится час.
    assert reserve(db_session, 1, 100, NOW) == pytest.approx(600)
    # Нужно 3 слота: второй и третий по старшинству — 40-минутные, ждать 20 минут.
    assert reserve(db_session, 3, 100, NOW) == pytest.approx(1200)
    assert calls_count(db_session) == 100


def test_calls_older_than_hour_do_not_count(db_session):
    add_calls(db_session, 100, minutes_ago=61)
    assert reserve(db_session, 3, 100, NOW) == 0.0


def test_calls_older_than_two_hours_are_deleted(db_session):
    add_calls(db_session, 5, minutes_ago=121)
    add_calls(db_session, 2, minutes_ago=90)
    reserve(db_session, 1, 100, NOW)
    assert calls_count(db_session) == 3


def test_count_above_limit_is_programming_error(db_session):
    with pytest.raises(ValueError):
        reserve(db_session, 3, 2, NOW)


def test_quota_exceeded_carries_seconds():
    assert QuotaExceeded(125.5).seconds == 125.5
