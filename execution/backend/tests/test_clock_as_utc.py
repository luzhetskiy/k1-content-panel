from datetime import datetime, timedelta, timezone

from app.clock import as_utc


def test_as_utc_marks_naive_as_utc():
    assert as_utc(datetime(2026, 9, 16, 12, 0)) == datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def test_as_utc_keeps_aware():
    moment = datetime(2026, 9, 16, 15, 0, tzinfo=timezone(timedelta(hours=3)))
    assert as_utc(moment) is moment
