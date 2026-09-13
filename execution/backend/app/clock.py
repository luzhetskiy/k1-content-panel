from datetime import datetime, timezone


def utcnow() -> datetime:
    """Единая точка получения времени — подменяется в тестах."""
    return datetime.now(timezone.utc)


def seconds_since(moment: datetime) -> float:
    """Сколько секунд прошло с `moment`, прочитанного из БД.

    Нужна потому, что один и тот же столбец `DateTime(timezone=True)` приходит
    по-разному: Postgres (прод) отдаёт datetime с таймзоной, SQLite (тесты) —
    naive. Прямое вычитание одного из другого падает с TypeError «can't
    subtract offset-naive and offset-aware datetimes», причём на проде
    не падает, а в тестах падает — то есть расхождение ловится только если
    тест есть. Naive-значение трактуем как UTC: именно в UTC пишет utcnow().

    Обёрнуто функцией, а не повторяется на месте вызова, чтобы следующий, кому
    понадобится «сколько прошло с момента X», не наступил на то же самое.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (utcnow() - moment).total_seconds()
