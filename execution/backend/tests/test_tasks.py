from types import SimpleNamespace

import pytest

from app.ai.text import JsonResult
from app.models.article import Article, ArticleBatch
from app.models.job import JobRun
from app.models.site import Site
from app.tasks import generate_topics_sync, retry_article_sync, run_batch_sync


@pytest.fixture
def site(db_session):
    from app.seed import seed_prompts

    seed_prompts(db_session)
    row = Site(name="Стройбаза", domain="x.ru", base_url="https://x.ru",
               api_token_enc="e", articles_parent_id=25,
               articles_url_prefix="/poleznye-stati/",
               site_description="Стройбаза в Самаре", tone_of_voice="практичный",
               reference_article_id=312, reference_html="<p>эталон</p><img><img>",
               reference_images=2)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def batch(db_session, site, admin):
    row = ArticleBatch(site_id=site.id, requested_count=3, created_by_id=admin.id)
    db_session.add(row)
    db_session.commit()
    return row


def patch_deps(monkeypatch, topics, existing=None):
    monkeypatch.setattr(
        "app.tasks.build_text_client",
        lambda db: SimpleNamespace(model="m",
                                   complete_json=lambda p: JsonResult(topics, 10, 20, 0.2)))
    monkeypatch.setattr(
        "app.tasks.open_site_client",
        lambda db, site: SimpleNamespace(
            list_section_pages=lambda prefix: existing or []))


def patch_deps_sequence(monkeypatch, topic_rounds, existing=None):
    """Как patch_deps, но каждый вызов complete_json отдаёт следующий список
    из topic_rounds (последний — для всех вызовов сверх длины списка) —
    для теста добора тем, где раунды должны отличаться."""
    calls = {"n": 0}

    def complete_json(prompt):
        index = min(calls["n"], len(topic_rounds) - 1)
        calls["n"] += 1
        return JsonResult(topic_rounds[index], 10, 20, 0.2)

    monkeypatch.setattr(
        "app.tasks.build_text_client",
        lambda db: SimpleNamespace(model="m", complete_json=complete_json))
    monkeypatch.setattr(
        "app.tasks.open_site_client",
        lambda db, site: SimpleNamespace(
            list_section_pages=lambda prefix: existing or []))
    return calls


def test_generate_topics_fills_articles(db_session, batch, monkeypatch):
    # Не "Тема А"/"Тема Б"/"Тема В": буквы-суффиксы А/В транслитерируются в
    # "a"/"v" — предлоги из _STOPWORDS (app/articles/topics.py) — и после
    # нормализации все три темы схлопываются в один и тот же токен {"tema"},
    # из-за чего filter_duplicates честно считает вторую и третью дублями
    # первой (найдено при прогоне этого теста: с этими строками проходит
    # только "Тема А"). Это не баг задачи 17, а уже документированное в
    # topics.py ограничение формулы (Task 15) — тестовые темы просто обязаны
    # быть такими, какие реально приходят от модели, а не буквенными ярлыками.
    patch_deps(monkeypatch, ["Утепление фасада минватой", "Выбор кровельного материала",
                            "Монтаж вентилируемого фасада"])
    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "topics_review"
    assert [a.topic for a in batch.articles] == [
        "Утепление фасада минватой", "Выбор кровельного материала",
        "Монтаж вентилируемого фасада"]


def test_generate_topics_drops_duplicates(db_session, batch, monkeypatch):
    patch_deps(monkeypatch, ["Чем утеплить каркасный дом", "Как выбрать кровлю"],
               existing=[{"title": "Чем утеплить каркасный дом", "url": "/blog/a/"}])
    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert [a.topic for a in batch.articles] == ["Как выбрать кровлю"]


def test_generate_topics_tops_up_after_duplicates(db_session, batch, monkeypatch):
    # batch.requested_count == 3 (см. фикстуру). Первый раунд отдаёт только
    # одну новую тему (вторая — дубль уже существующей на сайте), второй
    # раунд добирает недостающие две — итог должен закрыть все 3.
    calls = patch_deps_sequence(
        monkeypatch,
        [
            ["Чем утеплить каркасный дом", "Как выбрать кровлю"],
            ["Монтаж вентилируемого фасада", "Укладка тротуарной плитки"],
        ],
        existing=[{"title": "Чем утеплить каркасный дом", "url": "/blog/a/"}])
    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "topics_review"
    assert [a.topic for a in batch.articles] == [
        "Как выбрать кровлю", "Монтаж вентилируемого фасада",
        "Укладка тротуарной плитки"]
    assert calls["n"] == 2  # ровно один добор, не третий раунд впустую


def test_generate_topics_stops_after_max_topup_rounds(db_session, batch, monkeypatch):
    # Модель раз за разом предлагает один и тот же дубль — партия не должна
    # долбить в неё бесконечно, а завершиться с тем, что реально набралось.
    calls = patch_deps_sequence(
        monkeypatch, [["Чем утеплить каркасный дом"]],
        existing=[{"title": "Чем утеплить каркасный дом", "url": "/blog/a/"}])
    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "topics_review"
    assert batch.articles == []
    assert calls["n"] == 3  # первый раунд + 2 добора (_TOPICS_TOPUP_ROUNDS), не больше

    job = db_session.query(JobRun).filter_by(kind="generate_topics").one()
    assert "из 3 запрошенных" in job.log_text


def test_generate_topics_records_job_run(db_session, batch, monkeypatch):
    patch_deps(monkeypatch, ["Тема А"])
    generate_topics_sync(db_session, batch.id)
    job = db_session.query(JobRun).filter_by(kind="generate_topics").one()
    assert job.status == "ok"
    assert job.finished_at is not None


def test_generate_topics_failure_marks_batch(db_session, batch, monkeypatch):
    from app.ai.text import LLMError

    def broken(db):
        raise LLMError("LLM недоступна после 3 попыток")

    monkeypatch.setattr("app.tasks.build_text_client", broken)
    monkeypatch.setattr("app.tasks.open_site_client",
                        lambda db, site: SimpleNamespace(list_section_pages=lambda p: []))
    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "failed"
    assert "недоступна" in batch.error_text


def test_non_list_answer_marks_batch_failed(db_session, batch, monkeypatch):
    patch_deps(monkeypatch, {"topics": ["Тема"]})
    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "failed"
    assert "массив" in batch.error_text


def test_run_batch_builds_each_article(db_session, batch, site, monkeypatch):
    db_session.add_all([
        Article(batch_id=batch.id, site_id=site.id, topic="Тема А"),
        Article(batch_id=batch.id, site_id=site.id, topic="Тема Б"),
    ])
    batch.status = "topics_review"
    db_session.commit()

    built = []
    monkeypatch.setattr("app.tasks.build_for",
                        lambda db, article, site, site_client, job_run_id:
                        built.append(article.topic) or setattr(article, "status", "published"))
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: SimpleNamespace())

    run_batch_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert built == ["Тема А", "Тема Б"]
    assert batch.status == "done"


def test_run_batch_continues_after_single_failure(db_session, batch, site, monkeypatch):
    db_session.add_all([
        Article(batch_id=batch.id, site_id=site.id, topic="Тема А"),
        Article(batch_id=batch.id, site_id=site.id, topic="Тема Б"),
    ])
    batch.status = "topics_review"
    db_session.commit()

    def build(db, article, site, site_client, job_run_id):
        article.status = "failed" if article.topic == "Тема А" else "published"

    monkeypatch.setattr("app.tasks.build_for", build)
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: SimpleNamespace())

    run_batch_sync(db_session, batch.id)
    db_session.refresh(batch)
    # Одна упавшая статья не должна отменять остальные — партия всё равно done.
    assert batch.status == "done"
    assert {a.status for a in batch.articles} == {"failed", "published"}


# --- находка №1 ревью Task 17: AIConfigError (Task 13) не ловилась ни в
# одной из трёх *_sync-функций — она новее их исходного except-списка. ---

def test_generate_topics_ai_config_error_marks_batch_failed(db_session, batch, monkeypatch):
    from app.ai.factory import AIConfigError

    def broken(db):
        raise AIConfigError("ключ RouterAI не задан — заполните routerai_api_key")

    monkeypatch.setattr("app.tasks.build_text_client", broken)
    monkeypatch.setattr("app.tasks.open_site_client",
                        lambda db, site: SimpleNamespace(list_section_pages=lambda p: []))
    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "failed"
    assert "ключ" in batch.error_text


def test_run_batch_ai_config_error_marks_batch_failed(db_session, batch, site, monkeypatch):
    from app.ai.factory import AIConfigError

    db_session.add(Article(batch_id=batch.id, site_id=site.id, topic="Тема А"))
    batch.status = "topics_review"
    db_session.commit()

    def broken(db, article, site, site_client, job_run_id):
        raise AIConfigError("ключ RouterAI не задан — заполните routerai_api_key")

    monkeypatch.setattr("app.tasks.build_for", broken)
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: SimpleNamespace())

    run_batch_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "failed"
    assert "ключ" in batch.error_text


def test_run_batch_secret_decryption_error_on_site_client_marks_failed(
        db_session, batch, site, monkeypatch):
    from app.settings.crypto import SecretDecryptionError

    db_session.add(Article(batch_id=batch.id, site_id=site.id, topic="Тема А"))
    batch.status = "topics_review"
    db_session.commit()

    def broken(db, site):
        raise SecretDecryptionError("значение зашифровано другим ключом")

    monkeypatch.setattr("app.tasks.open_site_client", broken)
    monkeypatch.setattr("app.tasks.build_for", lambda *a, **k: None)

    run_batch_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "failed"
    assert "ключ" in batch.error_text or "зашифровано" in batch.error_text


def test_retry_article_ai_config_error_marks_failed(db_session, batch, site, monkeypatch):
    from app.ai.factory import AIConfigError

    article = Article(batch_id=batch.id, site_id=site.id, topic="Тема")
    db_session.add(article)
    db_session.commit()

    def broken(db, article, site, site_client, job_run_id):
        raise AIConfigError("ключ RouterAI не задан — заполните routerai_api_key")

    monkeypatch.setattr("app.tasks.build_for", broken)
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: SimpleNamespace())

    retry_article_sync(db_session, article.id)
    db_session.refresh(article)
    assert article.status == "failed"
    assert "ключ" in article.error_text


# --- находка №2 ревью Task 17: сайт партии/статьи мог быть удалён
# (site_id nullable, ON DELETE SET NULL, Task 14) — db.get(Site, ...) вернёт
# None, а без проверки упадёт AttributeError на первом же обращении к site.id.

def test_generate_topics_without_site_marks_batch_failed(db_session, admin, monkeypatch):
    batch = ArticleBatch(site_id=None, requested_count=1, created_by_id=admin.id)
    db_session.add(batch)
    db_session.commit()

    calls = []
    monkeypatch.setattr("app.tasks.build_text_client", lambda db: calls.append(1))

    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "failed"
    assert "удал" in batch.error_text
    assert calls == []  # до платного вызова дело не должно было дойти


def test_run_batch_without_site_marks_batch_failed(db_session, admin, monkeypatch):
    batch = ArticleBatch(site_id=None, requested_count=1, created_by_id=admin.id)
    db_session.add(batch)
    db_session.commit()
    db_session.add(Article(batch_id=batch.id, site_id=None, topic="Тема"))
    batch.status = "topics_review"
    db_session.commit()

    calls = []
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: calls.append(1))

    run_batch_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "failed"
    assert "удал" in batch.error_text
    assert calls == []


def test_retry_article_without_site_marks_article_failed(db_session, admin, monkeypatch):
    batch = ArticleBatch(site_id=None, requested_count=1, created_by_id=admin.id)
    db_session.add(batch)
    db_session.commit()
    article = Article(batch_id=batch.id, site_id=None, topic="Тема")
    db_session.add(article)
    db_session.commit()

    calls = []
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: calls.append(1))

    retry_article_sync(db_session, article.id)
    db_session.refresh(article)
    assert article.status == "failed"
    assert "удал" in article.error_text
    assert calls == []


# --- находка №3 ревью Task 17: generate_topics_sync не защищена от
# повторного запуска — без защиты каждый повтор задваивает Article.

def test_generate_topics_does_not_duplicate_on_rerun(db_session, batch, monkeypatch):
    calls = []

    def fake_build_text_client(db):
        calls.append(1)
        return SimpleNamespace(
            model="m", complete_json=lambda p: JsonResult(["Тема А"], 10, 20, 0.2))

    monkeypatch.setattr("app.tasks.build_text_client", fake_build_text_client)
    monkeypatch.setattr("app.tasks.open_site_client",
                        lambda db, site: SimpleNamespace(list_section_pages=lambda p: []))

    generate_topics_sync(db_session, batch.id)
    generate_topics_sync(db_session, batch.id)  # повторная постановка той же задачи

    db_session.refresh(batch)
    assert [a.topic for a in batch.articles] == ["Тема А"]
    assert len(calls) == 1


def test_generate_topics_retry_after_failure_is_allowed(db_session, batch, monkeypatch):
    from app.ai.text import LLMError

    def broken(db):
        raise LLMError("LLM недоступна")

    monkeypatch.setattr("app.tasks.build_text_client", broken)
    monkeypatch.setattr("app.tasks.open_site_client",
                        lambda db, site: SimpleNamespace(list_section_pages=lambda p: []))
    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "failed"

    patch_deps(monkeypatch, ["Тема А"])
    generate_topics_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "topics_review"
    assert [a.topic for a in batch.articles] == ["Тема А"]
