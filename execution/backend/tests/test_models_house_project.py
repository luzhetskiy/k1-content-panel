import pytest
from sqlalchemy.exc import IntegrityError

from app.models.house_project import HouseProjectPage, ProjectRewriteRun
from app.models.site import Site


@pytest.fixture
def site(db_session):
    row = Site(name="Стройбаза Самара", domain="stroybaza-samara.ru",
               base_url="https://stroybaza-samara.ru", api_token_enc="e")
    db_session.add(row)
    db_session.commit()
    return row


def test_site_projects_defaults(site):
    assert (site.projects_enabled, site.projects_parent_id, site.projects_parent_title) == \
        (False, None, "")


def test_page_defaults(db_session, site):
    page = HouseProjectPage(site_id=site.id, remote_id=16)
    db_session.add(page)
    db_session.commit()
    assert (page.status, page.original_html, page.rewritten_html, page.rewrite_count,
            page.rewritten_at, page.skip_reason) == ("new", "", "", 0, None, "")
    assert page.updated_at is not None


def test_page_unique_per_site(db_session, site):
    db_session.add_all([HouseProjectPage(site_id=site.id, remote_id=16),
                        HouseProjectPage(site_id=site.id, remote_id=16)])
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_run_defaults(db_session, site):
    run = ProjectRewriteRun(site_id=site.id)
    db_session.add(run)
    db_session.commit()
    assert (run.mode, run.total, run.error_text, run.finished_at) == ("new", 0, "", None)
    assert run.started_at is not None
