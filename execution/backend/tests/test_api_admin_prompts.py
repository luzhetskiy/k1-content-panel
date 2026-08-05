from types import SimpleNamespace

import pytest

from app.ai.text import LLMError, TextResult


@pytest.fixture
def seeded(db_session):
    from app.seed import seed_prompts

    seed_prompts(db_session)


@pytest.fixture
def site(db_session):
    from app.models.site import Site

    site = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e")
    db_session.add(site)
    db_session.commit()
    return site


def _stub(monkeypatch, **client):
    """Подменяет фабрику клиента: тесты API не ходят в платную модель."""
    monkeypatch.setattr("app.api.admin_prompts.build_text_client",
                        lambda db, **kwargs: SimpleNamespace(**client))


def test_manager_cannot_read_prompts(manager_client, seeded):
    assert manager_client.get("/api/admin/prompts").status_code == 403


def test_admin_lists_global_prompts(admin_client, seeded):
    body = admin_client.get("/api/admin/prompts").json()
    keys = {item["key"] for item in body if item["site_id"] is None}
    assert keys == {"topics", "article_body", "cover", "content_image"}


def test_admin_saves_site_override(admin_client, seeded, db_session, site):
    from app.ai.prompts import resolve_prompt

    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "topics", "site_id": site.id, "text": "свой промпт"})
    assert resp.status_code == 200
    assert resolve_prompt(db_session, "topics", site.id) == "свой промпт"


def test_saving_twice_updates_the_same_row(admin_client, seeded, db_session, site):
    """Второй PUT обязан обновлять строку, а не добавлять вторую: пара
    (key, site_id) уникальна, и без поиска существующей строки этот запрос
    падал бы IntegrityError-ом с 500 вместо 200."""
    from app.ai.prompts import resolve_prompt

    admin_client.put("/api/admin/prompts",
                     json={"key": "topics", "site_id": site.id, "text": "первый"})
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "topics", "site_id": site.id, "text": "второй"})
    assert resp.status_code == 200
    assert resolve_prompt(db_session, "topics", site.id) == "второй"
    assert len(admin_client.get("/api/admin/prompts").json()) == 5


def test_saving_global_prompt_replaces_default(admin_client, seeded, db_session):
    from app.ai.prompts import resolve_prompt

    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "cover", "site_id": None, "text": "новый глобальный"})
    assert resp.status_code == 200
    assert resolve_prompt(db_session, "cover", None) == "новый глобальный"
    assert len(admin_client.get("/api/admin/prompts").json()) == 4


def test_unknown_key_rejected(admin_client, seeded):
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "выдуманный", "text": "привет"})
    assert resp.status_code == 400
    assert "выдуманный" in resp.json()["detail"]


def test_unknown_site_rejected(admin_client, seeded):
    """Без этой проверки строка с несуществующим site_id либо ложится в БД
    (SQLite, внешние ключи выключены), либо валит commit необработанным
    IntegrityError — 500 вместо внятного 404."""
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "topics", "site_id": 999, "text": "текст"})
    assert resp.status_code == 404


def test_broken_template_is_rejected_on_save(admin_client, seeded):
    """Сломанный шаблон, сохранённый без проверки, взрывается позже — внутри
    Celery-задачи генерации статьи, где ошибку уже никто не свяжет с правкой
    промпта. Кнопка «Тест» тут не защита: сохранить можно и не нажав её."""
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "topics", "text": "{% for x in %}"})
    assert resp.status_code == 400
    assert "синтаксис" in resp.json()["detail"]


def test_empty_global_prompt_is_rejected(admin_client, seeded, db_session):
    """resolve_prompt отдаёт глобальный шаблон как есть, без проверки на
    пустоту (пустой текст — «использовать глобальный» — осмыслен только для
    переопределения сайта). Пустой глобальный шаблон означал бы пустой
    платный запрос в модель."""
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "topics", "site_id": None, "text": "   "})
    assert resp.status_code == 400


def test_empty_site_override_falls_back_to_global(admin_client, seeded, db_session, site):
    from app.ai.prompts import resolve_prompt
    from app.seed import DEFAULT_PROMPTS

    admin_client.put("/api/admin/prompts",
                     json={"key": "topics", "site_id": site.id, "text": "свой"})
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "topics", "site_id": site.id, "text": ""})
    assert resp.status_code == 200
    assert resolve_prompt(db_session, "topics", site.id) == DEFAULT_PROMPTS["topics"]


def test_concurrent_first_save_recovers(admin_client, seeded, db_session, site, monkeypatch):
    """Два админа сохраняют переопределение одного и того же ключа: оба
    проходят SELECT (строки нет) раньше, чем кто-то из них коммитит. Тот же
    класс гонки, что чинили в Task 5 для SettingsService._upsert."""
    from sqlalchemy import text as sql_text
    from sqlalchemy.exc import IntegrityError

    real_commit = db_session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            db_session.rollback()
            db_session.execute(
                sql_text("INSERT INTO prompt_templates (key, site_id, text) "
                         "VALUES ('cover', :site_id, 'от конкурента')"),
                {"site_id": site.id})
            real_commit()
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        real_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)
    resp = admin_client.put("/api/admin/prompts",
                            json={"key": "cover", "site_id": site.id, "text": "наш"})
    assert resp.status_code == 200
    assert calls["n"] == 2
    monkeypatch.undo()
    from app.ai.prompts import resolve_prompt
    assert resolve_prompt(db_session, "cover", site.id) == "наш"


def test_concurrent_first_seed_recovers(admin_client, db_session, monkeypatch):
    """GET сеет дефолты на каждом обращении (как и экран настроек), поэтому
    два админа на пустой БД сталкиваются на уникальном индексе
    uq_prompt_key_global. Проверено на живом Postgres 16: без обработки
    IntegrityError второй GET отвечает 500."""
    from sqlalchemy import text as sql_text
    from sqlalchemy.exc import IntegrityError

    real_commit = db_session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            db_session.rollback()
            db_session.execute(
                sql_text("INSERT INTO prompt_templates (key, site_id, text) "
                         "VALUES ('topics', NULL, 'от конкурента')"))
            real_commit()
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        real_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)
    resp = admin_client.get("/api/admin/prompts")
    assert resp.status_code == 200
    assert calls["n"] == 2
    keys = [item["key"] for item in resp.json()]
    assert sorted(keys) == ["article_body", "content_image", "cover", "topics"]


def test_test_endpoint_returns_rendered_prompt_and_answer(admin_client, seeded, monkeypatch):
    _stub(monkeypatch,
          complete_text=lambda prompt: TextResult("ответ модели", 10, 20, 0.3))

    resp = admin_client.post("/api/admin/prompts/test", json={
        "text": "Придумай {{ count }} тем для {{ site_name }}.",
        "variables": {"count": 3, "site_name": "Стройбаза"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["rendered"] == "Придумай 3 тем для Стройбаза."
    assert body["answer"] == "ответ модели"
    assert body["tokens_total"] == 30
    assert body["cost"] == 0.3


def test_test_endpoint_reports_template_error(admin_client, seeded):
    resp = admin_client.post("/api/admin/prompts/test",
                             json={"text": "{% for x in %}", "variables": {}})
    assert resp.status_code == 400
    assert "синтаксис" in resp.json()["detail"]


def test_test_endpoint_reports_unknown_variable(admin_client, seeded):
    """StrictUndefined (Task 12): опечатка в имени переменной обязана быть
    видна на экране «Тест», а не тихо вырезать кусок инструкции."""
    resp = admin_client.post("/api/admin/prompts/test",
                             json={"text": "тем: {{ conut }}", "variables": {"count": 3}})
    assert resp.status_code == 400
    assert "conut" in resp.json()["detail"]


def test_test_endpoint_requires_admin(manager_client, seeded):
    resp = manager_client.post("/api/admin/prompts/test",
                               json={"text": "привет", "variables": {}})
    assert resp.status_code == 403


def test_test_endpoint_does_not_call_model_on_empty_render(admin_client, seeded, monkeypatch):
    """Пустой отрендеренный промпт — это платный запрос ни о чём и заведомо
    мусорный ответ."""
    def boom(db, **kwargs):
        raise AssertionError("клиент не должен собираться для пустого промпта")

    monkeypatch.setattr("app.api.admin_prompts.build_text_client", boom)
    resp = admin_client.post("/api/admin/prompts/test",
                             json={"text": "{{ x }}", "variables": {"x": "  "}})
    assert resp.status_code == 400


def test_test_endpoint_reports_llm_failure(admin_client, seeded, monkeypatch):
    def failing(prompt):
        raise LLMError("RouterAI отклонил ключ API (401)")

    _stub(monkeypatch, complete_text=failing)
    resp = admin_client.post("/api/admin/prompts/test",
                             json={"text": "привет", "variables": {}})
    assert resp.status_code == 502
    assert "401" in resp.json()["detail"]


def test_test_endpoint_reports_missing_api_key(admin_client, seeded):
    """Ключ RouterAI не заполнен: админ обязан увидеть 400 с названием
    настройки, а не 502 после похода в RouterAI за 401."""
    resp = admin_client.post("/api/admin/prompts/test",
                             json={"text": "привет", "variables": {}})
    assert resp.status_code == 400
    assert "routerai_api_key" in resp.json()["detail"]


def test_test_endpoint_reports_wrong_encryption_key(admin_client, seeded, db_session):
    from cryptography.fernet import Fernet

    from app.config import config
    from app.settings.service import SettingsService

    SettingsService(db_session, config.encryption_key).set_secret("routerai_api_key", "sk-x")
    original = config.encryption_key
    config.encryption_key = Fernet.generate_key().decode()
    try:
        resp = admin_client.post("/api/admin/prompts/test",
                                 json={"text": "привет", "variables": {}})
    finally:
        config.encryption_key = original
    assert resp.status_code == 400
    assert "ENCRYPTION_KEY" in resp.json()["detail"]


def test_test_endpoint_bounds_waiting_time(admin_client, seeded, monkeypatch):
    """Синхронный эндпоинт с llm_max_retries=3 держал бы админа до ≈366 с
    (120 с таймаута × 3 попытки + паузы backoff, см. app/ai/text.py).
    Прогон делается одной попыткой: админ сидит перед экраном и нажмёт
    «Тест» ещё раз сам."""
    seen = {}

    def spy(db, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(complete_text=lambda prompt: TextResult("ок", 1, 1, 0.0))

    monkeypatch.setattr("app.api.admin_prompts.build_text_client", spy)
    admin_client.post("/api/admin/prompts/test", json={"text": "привет", "variables": {}})
    assert seen["max_retries"] == 1


def test_variables_default_is_not_shared_between_requests(admin_client, seeded, monkeypatch):
    """pydantic v2 копирует изменяемый дефолт на каждый экземпляр (проверено
    на pydantic 2.10.4), поэтому variables={} безопасен — тест фиксирует это
    как требование, а не как счастливую случайность."""
    from app.api.admin_prompts import PromptTestIn

    first = PromptTestIn(text="a")
    first.variables["x"] = 1
    assert PromptTestIn(text="b").variables == {}
