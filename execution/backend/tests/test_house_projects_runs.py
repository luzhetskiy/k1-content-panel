from datetime import timedelta

import pytest

from app.clock import utcnow
from app.house_projects.runs import (
    STALE_AFTER, STUCK_AFTER, abort_run, active_run, finish_run_if_complete, is_busy, is_lost,
    is_stale, is_stuck, next_queued, pages_for_mode, release_queued, run_counts,
)
from app.models.house_project import HouseProjectPage, ProjectRewriteRun
from app.models.job import JobRun
from app.models.site import Site


@pytest.fixture
def site(db_session):
    row = Site(name="С", domain="s.ru", base_url="https://s.ru", api_token_enc="e",
               projects_enabled=True, projects_parent_id=15)
    db_session.add(row)
    db_session.commit()
    return row


def add_page(db, site, remote_id, status="new", **fields):
    page = HouseProjectPage(site_id=site.id, remote_id=remote_id, status=status, **fields)
    db.add(page)
    db.commit()
    return page


def test_pages_for_mode(db_session, site):
    now = utcnow()
    add_page(db_session, site, 1, "new")
    add_page(db_session, site, 2, "done", rewritten_at=now)
    add_page(db_session, site, 3, "failed")
    add_page(db_session, site, 4, "skipped", skip_reason="удалена на сайте")
    add_page(db_session, site, 5, "in_work", started_at=now - STUCK_AFTER - timedelta(minutes=1))
    add_page(db_session, site, 6, "in_work", started_at=now)
    add_page(db_session, site, 7, "queued", updated_at=now - STALE_AFTER - timedelta(minutes=1))
    assert [p.remote_id for p in pages_for_mode(db_session, site.id, "new")] == [1, 3, 5, 7]
    assert [p.remote_id for p in pages_for_mode(db_session, site.id, "all")] == [1, 2, 3, 5, 6, 7]


def test_is_stuck_and_is_lost(db_session, site):
    now = utcnow()
    old = add_page(db_session, site, 1, "in_work", started_at=now - STUCK_AFTER - timedelta(seconds=1))
    fresh = add_page(db_session, site, 2, "in_work", started_at=now)
    assert is_stuck(old, now) and not is_stuck(fresh, now)
    lost = add_page(db_session, site, 3, "queued", updated_at=now - STALE_AFTER - timedelta(seconds=1))
    waiting = add_page(db_session, site, 4, "queued", updated_at=now)
    assert is_lost(lost, now) and not is_lost(waiting, now)


def test_run_counts_and_finish(db_session, site):
    run = ProjectRewriteRun(site_id=site.id, total=3, started_at=utcnow() - timedelta(minutes=5))
    db_session.add(run)
    db_session.commit()
    add_page(db_session, site, 1, "done")
    add_page(db_session, site, 2, "failed", updated_at=utcnow())
    add_page(db_session, site, 3, "queued", updated_at=utcnow())
    assert run_counts(db_session, run) == (1, 1)
    assert next_queued(db_session, site.id).remote_id == 3
    assert finish_run_if_complete(db_session, site.id) is None

    db_session.query(HouseProjectPage).filter_by(remote_id=3).one().status = "done"
    db_session.commit()
    assert finish_run_if_complete(db_session, site.id) is run
    assert run.finished_at is not None and active_run(db_session, site.id) is None


def test_old_failure_does_not_count_in_new_run(db_session, site):
    run = ProjectRewriteRun(site_id=site.id, total=1, started_at=utcnow())
    db_session.add(run)
    add_page(db_session, site, 1, "failed", updated_at=utcnow() - timedelta(days=1))
    assert run_counts(db_session, run) == (1, 0)


def test_stale_run(db_session, site):
    now = utcnow()
    run = ProjectRewriteRun(site_id=site.id, total=2, started_at=now - STALE_AFTER - timedelta(minutes=1))
    db_session.add(run)
    queued = add_page(db_session, site, 1, "queued", updated_at=now - STALE_AFTER - timedelta(minutes=1))
    assert is_stale(db_session, run, now)
    queued.updated_at = now
    db_session.commit()
    assert not is_stale(db_session, run, now)


def test_working_page_keeps_run_alive(db_session, site):
    now = utcnow()
    run = ProjectRewriteRun(site_id=site.id, total=1, started_at=now - STALE_AFTER * 2)
    db_session.add(run)
    add_page(db_session, site, 1, "in_work", started_at=now - timedelta(minutes=1),
             updated_at=now - STALE_AFTER * 2)
    assert not is_stale(db_session, run, now)


def test_is_busy(db_session, site):
    now = utcnow()
    assert not is_busy(db_session, site.id, now)
    page = add_page(db_session, site, 1, "queued", updated_at=now)
    assert is_busy(db_session, site.id, now)          # отдельная страница в очереди
    page.updated_at = now - STALE_AFTER - timedelta(minutes=1)
    db_session.commit()
    assert not is_busy(db_session, site.id, now)      # сообщение Celery потеряно
    run = ProjectRewriteRun(site_id=site.id, started_at=now)
    db_session.add(run)
    db_session.commit()
    assert is_busy(db_session, site.id, now)          # идёт синхронизация запуска


def test_release_and_abort(db_session, site):
    job = JobRun(kind="project_rewrite_run", status="running")
    db_session.add(job)
    db_session.commit()
    run = ProjectRewriteRun(site_id=site.id, total=2, job_run_id=job.id)
    db_session.add(run)
    db_session.commit()
    done_before = add_page(db_session, site, 1, "queued", rewritten_at=utcnow())
    never = add_page(db_session, site, 2, "queued")
    abort_run(db_session, run, "прерван")
    assert (done_before.status, never.status) == ("done", "new")
    assert run.error_text == "прерван" and run.finished_at is not None
    assert (job.status, job.log_text) == ("failed", "прерван")
    release_queued(db_session, site.id)    # повторно — без эффекта
    assert never.status == "new"
