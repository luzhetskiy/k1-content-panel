from datetime import datetime, timedelta, timezone

import pytest

from app.category_meta.runs import (
    active_run, finish_run_if_complete, is_stale, is_stuck, last_successful_run, next_queued,
    run_progress,
)
from app.models.category_meta import CategoryMeta, MetaRun
from app.models.site import Site

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def site(db_session):
    row = Site(name="С", domain="s.ru", base_url="https://s.ru", api_token_enc="e")
    db_session.add(row)
    db_session.commit()
    return row


def add_category(db, site, remote_id, status, *, minutes_ago=0, started_minutes_ago=None,
                 wait_in_minutes=None):
    row = CategoryMeta(site_id=site.id, remote_id=remote_id, name=f"К{remote_id}", status=status,
                       updated_at=NOW - timedelta(minutes=minutes_ago))
    if started_minutes_ago is not None:
        row.started_at = NOW - timedelta(minutes=started_minutes_ago)
    if wait_in_minutes is not None:
        row.wait_until = NOW + timedelta(minutes=wait_in_minutes)
    db.add(row)
    db.commit()
    return row


def add_run(db, site, minutes_ago=0, **kwargs):
    run = MetaRun(site_id=site.id, started_at=NOW - timedelta(minutes=minutes_ago), **kwargs)
    db.add(run)
    db.commit()
    return run


def test_is_stuck_after_35_minutes(db_session, site):
    # мягкий лимит задачи категории — 1900 с ≈ 32 мин; раньше него «зависла» не показываем
    assert not is_stuck(add_category(db_session, site, 1, "in_work", started_minutes_ago=34), NOW)
    assert is_stuck(add_category(db_session, site, 2, "in_work", started_minutes_ago=36), NOW)
    assert not is_stuck(add_category(db_session, site, 3, "done", started_minutes_ago=90), NOW)


def test_next_queued_in_id_order(db_session, site):
    add_category(db_session, site, 1, "done")
    second = add_category(db_session, site, 2, "queued")
    add_category(db_session, site, 3, "queued")
    assert next_queued(db_session, site.id).id == second.id


def test_fresh_run_is_not_stale(db_session, site):
    run = add_run(db_session, site, minutes_ago=5)
    add_category(db_session, site, 1, "queued", minutes_ago=5)
    assert not is_stale(db_session, run, NOW)


def test_run_without_signs_of_life_is_stale(db_session, site):
    run = add_run(db_session, site, minutes_ago=180)
    add_category(db_session, site, 1, "queued", minutes_ago=150)
    add_category(db_session, site, 2, "in_work", minutes_ago=150, started_minutes_ago=150)
    assert is_stale(db_session, run, NOW)


def test_waiting_for_quota_is_not_stale(db_session, site):
    run = add_run(db_session, site, minutes_ago=300)
    add_category(db_session, site, 1, "queued", minutes_ago=200, wait_in_minutes=20)
    assert not is_stale(db_session, run, NOW)


def test_working_category_is_not_stale(db_session, site):
    run = add_run(db_session, site, minutes_ago=300)
    add_category(db_session, site, 1, "in_work", minutes_ago=200, started_minutes_ago=3)
    assert not is_stale(db_session, run, NOW)


def test_progress_counts_and_nearest_wait(db_session, site):
    run = add_run(db_session, site, total=4)
    add_category(db_session, site, 1, "done")
    add_category(db_session, site, 2, "failed")
    add_category(db_session, site, 3, "queued", wait_in_minutes=40)
    add_category(db_session, site, 4, "queued", wait_in_minutes=10)
    add_category(db_session, site, 5, "skipped")
    progress = run_progress(db_session, run, NOW)
    assert (progress.total, progress.done, progress.failed) == (4, 1, 1)
    assert progress.wait_until.replace(tzinfo=timezone.utc) == NOW + timedelta(minutes=10)
    assert progress.stale is False


def test_finish_run_only_when_nothing_active(db_session, site):
    run = add_run(db_session, site)
    queued = add_category(db_session, site, 1, "queued")
    assert finish_run_if_complete(db_session, site.id, NOW) is None
    queued.status = "done"
    db_session.commit()
    assert finish_run_if_complete(db_session, site.id, NOW).id == run.id
    assert active_run(db_session, site.id) is None


def test_last_successful_run_ignores_failed(db_session, site):
    ok = add_run(db_session, site, minutes_ago=60, finished_at=NOW - timedelta(minutes=30))
    add_run(db_session, site, minutes_ago=20, finished_at=NOW - timedelta(minutes=10),
            error_text="сайт недоступен")
    assert last_successful_run(db_session, site.id).id == ok.id
