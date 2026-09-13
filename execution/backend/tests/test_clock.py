"""seconds_since — единственное место, где сравнивается хранимое в БД время с
текущим. Оба случая проверяются явно, потому что расхождение между контурами
(Postgres отдаёт aware, SQLite — naive) иначе всплывает как TypeError в
продовом коде, который локальные тесты прошли."""

from datetime import datetime, timedelta, timezone

from app.clock import seconds_since


def test_seconds_since_aware_datetime():
    """Так приходит из Postgres."""
    moment = datetime.now(timezone.utc) - timedelta(seconds=120)
    assert 119 <= seconds_since(moment) <= 130


def test_seconds_since_naive_datetime_is_treated_as_utc():
    """Так приходит из SQLite: naive-значение нельзя вычитать из aware напрямую
    («can't subtract offset-naive and offset-aware datetimes»), и трактовать
    его надо как UTC — именно в UTC пишет utcnow()."""
    moment = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=120)
    assert 119 <= seconds_since(moment) <= 130


def test_seconds_since_is_not_negative_for_just_now():
    assert seconds_since(datetime.now(timezone.utc)) >= 0
