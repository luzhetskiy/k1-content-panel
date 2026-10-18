import pytest

from app.ai.factory import AIConfigError
from app.ai.text import LLMError, TextResult
from app.clock import utcnow
from app.models.house_project import HouseProjectPage, ProjectRewriteRun
from app.models.job import JobRun, LlmUsage
from app.models.site import Site
from app.sites.client import SiteAPIError
from app.tasks import rewrite_project_page_sync, start_rewrite_run_sync

ORIGINAL = ("<p>Представляем проект уютного дома площадью 82.64 м², идеально подходящего "
            "для семьи из четырёх человек.</p>"
            "<ul><li>Монолитная плита под всем домом.</li>"
            "<li>Терраса 9.06 м² для отдыха.</li></ul>")
GOOD = ("<p>Перед вами комфортный дом на 82,64 м², который отлично подойдёт "
        "семье, где растут двое детей.</p>"
        "<ul><li>Под зданием залита монолитная плита.</li>"
        "<li>Для отдыха есть терраса 9.06 м².</li></ul>")


def page_text(inner=ORIGINAL):
    return ('<section class="project-description"><div class="product-info__text">'
            f'{inner}</div></section>')


class FakeSite:
    def __init__(self, children, list_error=None):
        self.children = children          # remote_id → текст страницы
        self.list_error = list_error
        self.patches = []

    def list_section_pages(self, prefix):
        if self.list_error:
            raise self.list_error
        return [{"id": 15}] + [{"id": pid} for pid in self.children]

    def get_page(self, page_id):
        if page_id == 15:
            return {"id": 15, "parent": None, "title": "Проекты домов", "text": ""}
        return {"id": page_id, "parent": 15, "title": f"Проект {page_id}",
                "url": f"/pr/{page_id}/", "text": self.children[page_id]}

    def update_page_text(self, page_id, html, *, as_is=False):
        self.patches.append(page_id)
        self.children[page_id] = html
        return {"id": page_id}


class FakeLLM:
    model = "m"

    def __init__(self, answer=GOOD, error=None):
        self.answer, self.error = answer, error

    def complete_text(self, prompt, *, reasoning=True):
        if self.error:
            raise self.error
        return TextResult(self.answer, 10, 5, 0.2)


@pytest.fixture
def site(db_session):
    from app.seed import seed_prompts

    seed_prompts(db_session)
    row = Site(name="Стройбаза", domain="s.ru", base_url="https://s.ru", api_token_enc="e",
               projects_enabled=True, projects_parent_id=15)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def patch(monkeypatch):
    def apply(site_client, llm=None):
        monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: site_client)
        if isinstance(llm, Exception):
            def broken(db):
                raise llm
            monkeypatch.setattr("app.tasks.build_text_client", broken)
        else:
            monkeypatch.setattr("app.tasks.build_text_client", lambda db: llm or FakeLLM())
        monkeypatch.setattr("app.house_projects.sync.time.sleep", lambda s: None)
    return apply


def new_run(db, site, mode="new"):
    run = ProjectRewriteRun(site_id=site.id, mode=mode, started_at=utcnow())
    db.add(run)
    db.commit()
    return run


def pages(db, site):
    return {p.remote_id: p for p in db.query(HouseProjectPage).filter_by(site_id=site.id).all()}


def test_start_syncs_and_queues_new_pages(db_session, site, patch):
    patch(FakeSite({16: page_text(), 17: page_text()}))
    run = new_run(db_session, site)
    first = start_rewrite_run_sync(db_session, run.id)
    rows = pages(db_session, site)
    assert first == rows[16].id
    assert {r.status for r in rows.values()} == {"queued"}
    assert run.total == 2
    assert db_session.get(JobRun, run.job_run_id).kind == "project_rewrite_run"


def test_mode_new_skips_done_and_all_takes_them(db_session, site, patch):
    db_session.add(HouseProjectPage(site_id=site.id, remote_id=16, status="done",
                                    rewritten_at=utcnow()))
    db_session.commit()
    patch(FakeSite({16: page_text(), 17: page_text()}))
    run = new_run(db_session, site, "new")
    start_rewrite_run_sync(db_session, run.id)
    assert (run.total, pages(db_session, site)[16].status) == (1, "done")

    for row in pages(db_session, site).values():
        row.status = "done"
    run.finished_at = utcnow()
    db_session.commit()
    second = new_run(db_session, site, "all")
    start_rewrite_run_sync(db_session, second.id)
    assert second.total == 2


def test_nothing_to_do_closes_run(db_session, site, patch):
    patch(FakeSite({}))
    run = new_run(db_session, site)
    assert start_rewrite_run_sync(db_session, run.id) is None
    assert run.finished_at is not None and run.error_text == ""
    assert db_session.get(JobRun, run.job_run_id).status == "ok"


def test_sync_failure_fails_run_and_keeps_statuses(db_session, site, patch):
    db_session.add(HouseProjectPage(site_id=site.id, remote_id=16, status="new"))
    db_session.commit()
    patch(FakeSite({}, list_error=SiteAPIError("список страниц: HTTP 401", status_code=401)))
    run = new_run(db_session, site)
    assert start_rewrite_run_sync(db_session, run.id) is None
    assert "HTTP 401" in run.error_text and run.finished_at is not None
    assert pages(db_session, site)[16].status == "new"


def test_chain_rewrites_pages_and_finishes_run(db_session, site, patch):
    client = FakeSite({16: page_text(), 17: page_text()})
    patch(client)
    run = new_run(db_session, site)
    first = start_rewrite_run_sync(db_session, run.id)
    following = rewrite_project_page_sync(db_session, first)
    assert following == pages(db_session, site)[17].id
    assert rewrite_project_page_sync(db_session, following) is None
    rows = pages(db_session, site)
    assert {r.status for r in rows.values()} == {"done"}
    assert client.patches == [16, 17]
    assert run.finished_at is not None
    job = db_session.get(JobRun, run.job_run_id)
    assert (job.status, job.log_text) == ("ok", "готово 2/2, ошибок 0")
    assert db_session.query(LlmUsage).filter_by(job_run_id=job.id).count() == 2


def test_llm_failure_fails_page_and_chain_goes_on(db_session, site, patch):
    patch(FakeSite({16: page_text(), 17: page_text()}),
          FakeLLM(error=LLMError("LLM недоступна после 3 попыток")))
    run = new_run(db_session, site)
    first = start_rewrite_run_sync(db_session, run.id)
    following = rewrite_project_page_sync(db_session, first)
    page = pages(db_session, site)[16]
    assert (page.status, page.error_text) == ("failed", "LLM недоступна после 3 попыток")
    assert following == pages(db_session, site)[17].id


def test_config_error_fails_whole_queue(db_session, site, patch):
    patch(FakeSite({16: page_text(), 17: page_text()}),
          AIConfigError("ключ RouterAI не задан"))
    run = new_run(db_session, site)
    first = start_rewrite_run_sync(db_session, run.id)
    assert rewrite_project_page_sync(db_session, first) is None
    assert {r.status for r in pages(db_session, site).values()} == {"failed"}
    assert run.finished_at is not None
    assert db_session.get(JobRun, run.job_run_id).status == "failed"


def test_single_page_rewrite_has_own_job_and_no_chain(db_session, site, patch):
    client = FakeSite({16: page_text(), 17: page_text()})
    patch(client)
    a = HouseProjectPage(site_id=site.id, remote_id=16, status="queued")
    b = HouseProjectPage(site_id=site.id, remote_id=17, status="queued")
    db_session.add_all([a, b])
    db_session.commit()
    assert rewrite_project_page_sync(db_session, a.id, continue_run=False) is None
    assert (a.status, b.status) == ("done", "queued")
    job = db_session.query(JobRun).filter_by(kind="project_rewrite_page").one()
    assert job.status == "ok"


def test_page_taken_by_other_task_is_not_processed_twice(db_session, site, patch):
    client = FakeSite({16: page_text(), 17: page_text()})
    patch(client)
    taken = HouseProjectPage(site_id=site.id, remote_id=16, status="in_work",
                             started_at=utcnow())
    waiting = HouseProjectPage(site_id=site.id, remote_id=17, status="queued")
    db_session.add_all([taken, waiting])
    db_session.commit()
    # Цепочку ведёт задача, взявшая страницу 16: прыжок к 17 отсюда дал бы вторую.
    assert rewrite_project_page_sync(db_session, taken.id) is None
    assert client.patches == [] and waiting.status == "queued"


def test_late_start_of_closed_run_does_nothing(db_session, site, patch):
    client = FakeSite({16: page_text()})
    patch(client)
    run = new_run(db_session, site)
    run.finished_at, run.error_text = utcnow(), "прерван"
    db_session.commit()
    assert start_rewrite_run_sync(db_session, run.id) is None
    assert pages(db_session, site) == {}
    assert run.job_run_id is None
