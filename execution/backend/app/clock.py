from datetime import datetime, timezone


def utcnow() -> datetime:
    """Единая точка получения времени — подменяется в тестах."""
    return datetime.now(timezone.utc)
