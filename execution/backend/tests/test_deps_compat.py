"""Совместимость закреплённых зависимостей broker-транспорта Celery.

Инцидент 2026-11-19 (партия 171): пересборка образа подняла kombu до 5.7.0
(celery 5.4.0 разрешает kombu<6.0), а её redis-транспорт требует redis-py
>=6.1.0 на рантайме — при закреплённом redis==5.2.1 (и ограничении
celery[redis] redis<6.0) воркер падал в цикле перезапуска, задачи не
ставились в очередь, партии зависали в running. pip check молчит:
требование живёт в extra kombu[redis], который не включён.

Тест ловит несовместимость БЕЗ живого Redis: VersionMismatch kombu бросает
до сетевого обращения (в _get_client при создании канала), а сетевой отказ
ожидаем и допустим — в тестах брокера нет.
"""

import pytest


def test_kombu_redis_transport_accepts_pinned_redis():
    from kombu import Connection
    from kombu.exceptions import VersionMismatch

    conn = Connection("redis://localhost:6379/0")
    try:
        conn.ensure_connection(max_retries=0)
    except VersionMismatch as exc:
        pytest.fail(f"kombu несовместим с закреплённым redis-py: {exc}")
    except Exception:
        pass  # сетевой отказ без живого Redis — ожидаем
    finally:
        conn.release()
