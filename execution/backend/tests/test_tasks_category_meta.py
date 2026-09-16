from types import SimpleNamespace

import pytest

from app.ai.factory import AIConfigError
from app.ai.text import JsonResult, LLMError
from app.models.category_meta import CategoryMeta, MetaRun
from app.models.job import JobRun, LlmUsage
from app.models.site import Site
from app.sites.client import SiteAPIError
from app.tasks import (
    MAX_QUOTA_COUNTDOWN_SECONDS, SEED_MISSING_TEXT, generate_category_meta_sync,
    start_meta_run_sync,
)
from app.wordstat.client import WordstatAuthError, WordstatError
from app.wordstat.quota import QuotaExceeded

CATEGORIES = [
    {"id": 45, "name": "Листовые материалы", "slug": "listovye-materialy", "parent": None,
     "published": True},
    {"id": 46, "name": "Фанера", "slug": "fanera", "parent": 45, "published": True},
    {"id": 70, "name": "Водосток", "slug": "vodostok", "parent": None, "published": False},
]


@pytest.fixture
def site(db_session):
    from app.seed import seed_prompts

    seed_prompts(db_session)
    row = Site(name="Стройбаза", domain="s.ru", base_url="https://s.ru", api_token_enc="e",
               city="Москва", city_in="в Москве", wordstat_region_id=213, brand="Стройбаза",
               meta_enabled=True)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def run(db_session, site, admin):
    row = MetaRun(site_id=site.id, created_by_id=admin.id)
    db_session.add(row)
    db_session.commit()
    return row


def seeds_answer(ids):
    return [{"id": i, "phrase": f"ф{i}", "nominative": f"ф{i}", "buy": f"купить ф{i}",
             "price": f"цена ф{i}"} for i in ids]


def patch_start(monkeypatch, *, answer=None, categories=CATEGORIES, list_error=None):
    def list_catalog_categories():
        if list_error:
            raise list_error
        return categories

    client = SimpleNamespace(list_catalog_categories=list_catalog_categories,
                             fetch_sitemap_paths=lambda: {"/catalog/listovye-materialy/",
                                                          "/catalog/category/listovye-materialy/fanera/"})
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: client)
    text = SimpleNamespace(model="m", prompts=[])

    def complete_json(prompt):
        text.prompts.append(prompt)
        return JsonResult(answer if answer is not None else seeds_answer([45, 46]), 10, 5, 0.2)

    text.complete_json = complete_json
    monkeypatch.setattr("app.tasks.build_text_client", lambda db: text)
    return text


def categories_by_remote(db, site):
    return {c.remote_id: c for c in db.query(CategoryMeta).filter_by(site_id=site.id).all()}


def test_start_queues_categories_and_returns_first(db_session, site, run, monkeypatch):
    patch_start(monkeypatch)
    first = start_meta_run_sync(db_session, run.id)
    rows = categories_by_remote(db_session, site)
    assert first == rows[45].id
    assert (rows[45].status, rows[46].status, rows[70].status) == ("queued", "queued", "skipped")
    assert rows[46].form_buy == "купить ф46"
    assert run.total == 2 and run.finished_at is None
    job = db_session.get(JobRun, run.job_run_id)
    assert job.kind == "category_meta_run" and job.status == "running"
    assert [u.cost for u in db_session.query(LlmUsage).all()] == [0.2]


def test_start_does_not_ask_llm_for_known_seeds(db_session, site, run, monkeypatch):
    for remote_id, name in ((45, "Листовые материалы"), (46, "Фанера")):
        db_session.add(CategoryMeta(site_id=site.id, remote_id=remote_id, name=name,
                                    seed_phrase="x", seed_source_name=name, status="done"))
    db_session.commit()
    text = patch_start(monkeypatch)
    start_meta_run_sync(db_session, run.id)
    assert text.prompts == []
    assert categories_by_remote(db_session, site)[46].status == "queued"


def test_start_marks_categories_without_seed_failed(db_session, site, run, monkeypatch):
    patch_start(monkeypatch, answer=seeds_answer([46]))
    first = start_meta_run_sync(db_session, run.id)
    rows = categories_by_remote(db_session, site)
    assert (rows[45].status, rows[45].error_text) == ("failed", SEED_MISSING_TEXT)
    assert first == rows[46].id


def test_start_site_error_fails_run(db_session, site, run, monkeypatch):
    patch_start(monkeypatch, list_error=SiteAPIError("список категорий каталога: HTTP 403"))
    assert start_meta_run_sync(db_session, run.id) is None
    assert run.finished_at is not None
    assert run.error_text == "список категорий каталога: HTTP 403"
    assert db_session.get(JobRun, run.job_run_id).status == "failed"


def test_start_with_nothing_to_do_closes_run(db_session, site, run, monkeypatch):
    patch_start(monkeypatch, categories=[CATEGORIES[2]])
    assert start_meta_run_sync(db_session, run.id) is None
    assert run.finished_at is not None and run.error_text == ""


# --- задача категории ---

@pytest.fixture
def queued(db_session, site):
    rows = [CategoryMeta(site_id=site.id, remote_id=i, name=f"К{i}", status="queued",
                         seed_phrase=f"ф{i}", form_nominative=f"ф{i}", form_buy=f"купить ф{i}",
                         url=f"/catalog/k{i}/") for i in (1, 2, 3)]
    db_session.add_all(rows)
    db_session.commit()
    return rows


def patch_category(monkeypatch, behaviour):
    """behaviour(category, kwargs) — вместо generate_category."""
    monkeypatch.setattr("app.tasks.build_text_client", lambda db: SimpleNamespace(model="m"))
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: SimpleNamespace())

    def fake_generate(db, category, site, **kwargs):
        behaviour(category, kwargs)
        db.commit()

    monkeypatch.setattr("app.tasks.generate_category", fake_generate)


def succeed(category, kwargs):
    kwargs["record_usage"](10, 5, 0.1)
    category.status = "done"


def test_success_continues_chain_and_logs_to_run_job(db_session, site, queued, monkeypatch):
    job = JobRun(kind="category_meta_run", site_id=site.id)
    db_session.add(job)
    db_session.commit()
    run = MetaRun(site_id=site.id, job_run_id=job.id, total=3)
    db_session.add(run)
    db_session.commit()
    patch_category(monkeypatch, succeed)
    assert generate_category_meta_sync(db_session, queued[0].id) == (None, queued[1].id)
    assert queued[0].status == "done"
    assert [u.job_run_id for u in db_session.query(LlmUsage).all()] == [job.id]
    assert run.finished_at is None


def test_last_category_closes_run_and_job(db_session, site, queued, monkeypatch):
    queued[1].status = queued[2].status = "done"
    job = JobRun(kind="category_meta_run", site_id=site.id)
    db_session.add(job)
    db_session.commit()
    run = MetaRun(site_id=site.id, job_run_id=job.id, total=3)
    db_session.add(run)
    db_session.commit()
    patch_category(monkeypatch, succeed)
    assert generate_category_meta_sync(db_session, queued[0].id) == (None, None)
    assert run.finished_at is not None
    assert (job.status, job.log_text) == ("ok", "готово 3/3, ошибок 0")


def test_quota_requeues_with_capped_countdown(db_session, site, queued, monkeypatch):
    def no_quota(category, kwargs):
        raise QuotaExceeded(2400)

    patch_category(monkeypatch, no_quota)
    countdown, following = generate_category_meta_sync(db_session, queued[0].id)
    assert (countdown, following) == (MAX_QUOTA_COUNTDOWN_SECONDS, None)
    assert queued[0].status == "queued"
    assert queued[0].wait_until is not None


def test_category_error_is_recorded_and_chain_goes_on(db_session, site, queued, monkeypatch):
    def llm_down(category, kwargs):
        raise LLMError("LLM недоступна после 3 попыток")

    patch_category(monkeypatch, llm_down)
    assert generate_category_meta_sync(db_session, queued[0].id) == (None, queued[1].id)
    assert (queued[0].status, queued[0].error_text) == ("failed", "LLM недоступна после 3 попыток")


def test_wordstat_error_fails_only_this_category(db_session, site, queued, monkeypatch):
    def wordstat_down(category, kwargs):
        raise WordstatError("Wordstat topRequests: HTTP 503")

    patch_category(monkeypatch, wordstat_down)
    generate_category_meta_sync(db_session, queued[0].id)
    assert [c.status for c in queued] == ["failed", "queued", "queued"]


@pytest.mark.parametrize("error", [WordstatAuthError("ключ Wordstat отклонён", 401),
                                   AIConfigError("ключ RouterAI не задан")])
def test_config_error_stops_whole_run(db_session, site, queued, monkeypatch, error):
    run = MetaRun(site_id=site.id, total=3)
    db_session.add(run)
    db_session.commit()

    def broken(category, kwargs):
        raise error

    patch_category(monkeypatch, broken)
    assert generate_category_meta_sync(db_session, queued[0].id) == (None, None)
    assert [c.status for c in queued] == ["failed", "failed", "failed"]
    assert all(c.error_text == str(error) for c in queued)
    assert run.finished_at is not None


def test_unexpected_error_does_not_break_chain(db_session, site, queued, monkeypatch):
    def boom(category, kwargs):
        raise KeyError("id")

    patch_category(monkeypatch, boom)
    assert generate_category_meta_sync(db_session, queued[0].id) == (None, queued[1].id)
    assert queued[0].error_text == "непредвиденная ошибка: KeyError: 'id'"


def test_already_taken_category_is_skipped_but_chain_continues(db_session, site, queued,
                                                               monkeypatch):
    queued[0].status = "in_work"
    db_session.commit()
    patch_category(monkeypatch, lambda category, kwargs: pytest.fail("не должна запускаться"))
    assert generate_category_meta_sync(db_session, queued[0].id) == (None, queued[1].id)
    assert generate_category_meta_sync(db_session, queued[0].id, continue_run=False) == (None, None)


def test_regenerate_uses_own_job_and_does_not_continue(db_session, site, queued, monkeypatch):
    queued[1].status = queued[2].status = "done"
    db_session.commit()
    patch_category(monkeypatch, succeed)
    assert generate_category_meta_sync(db_session, queued[0].id, continue_run=False) == (None, None)
    job = db_session.query(JobRun).filter_by(kind="category_meta").one()
    assert job.status == "ok"
    assert db_session.query(LlmUsage).one().job_run_id == job.id


def test_regenerate_without_llm_call_creates_no_job(db_session, site, queued, monkeypatch):
    def no_quota(category, kwargs):
        raise QuotaExceeded(30)

    patch_category(monkeypatch, no_quota)
    assert generate_category_meta_sync(db_session, queued[0].id, continue_run=False) == (30, None)
    assert db_session.query(JobRun).count() == 0
