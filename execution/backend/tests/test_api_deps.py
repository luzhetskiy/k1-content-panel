"""`conftest.py` подменяет `get_db` на `lambda: db_session` для всех
API-тестов (см. `_client_for`), поэтому настоящий генератор с
`try/finally: db.close()` не выполняется НИ В ОДНОМ тесте `test_api_auth.py`
— рефакторинг, потерявший `finally`, уехал бы в проде зелёным. Эти тесты
гоняют сам генератор `get_db` напрямую, без FastAPI и без реальной БД:
`SessionLocal` подменяется на фабрику фейковой сессии, интересен только факт
вызова `close()`.
"""

import pytest

from app.api import deps


class _FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_get_db_closes_session_after_normal_use(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(deps, "SessionLocal", lambda: session)

    gen = deps.get_db()
    yielded = next(gen)
    assert yielded is session
    assert session.closed is False

    with pytest.raises(StopIteration):
        next(gen)
    assert session.closed is True


def test_get_db_closes_session_when_caller_raises(monkeypatch):
    """FastAPI закрывает generator-зависимости через `gen.throw(...)`, если
    обработка запроса упала с исключением — `finally` обязан сработать и в
    этом случае, а не только при штатном завершении."""
    session = _FakeSession()
    monkeypatch.setattr(deps, "SessionLocal", lambda: session)

    gen = deps.get_db()
    next(gen)
    with pytest.raises(ValueError):
        gen.throw(ValueError("запрос упал"))
    assert session.closed is True
