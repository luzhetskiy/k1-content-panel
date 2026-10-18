import pytest

from app.house_projects.sync import NOT_ON_SITE, sync_pages, with_retries
from app.models.house_project import HouseProjectPage
from app.models.site import Site
from app.sites.client import SiteAPIError


class FakeSite:
    def __init__(self, pages):
        self.pages = pages          # id → карточка
        self.reads = []

    def list_section_pages(self, prefix):
        assert prefix == ""
        return [{"id": pid, "title": p["title"], "url": p["url"]} for pid, p in self.pages.items()]

    def get_page(self, page_id):
        self.reads.append(page_id)
        return {"id": page_id, **self.pages[page_id]}


def card(title, url, parent):
    return {"title": title, "url": url, "parent": parent, "text": "<p>x</p>"}


@pytest.fixture
def site(db_session):
    row = Site(name="Стройбаза Самара", domain="stroybaza-samara.ru",
               base_url="https://stroybaza-samara.ru", api_token_enc="e",
               projects_enabled=True, projects_parent_id=15)
    db_session.add(row)
    db_session.commit()
    return row


def pages_of(db, site):
    return {p.remote_id: p for p in db.query(HouseProjectPage).filter_by(site_id=site.id).all()}


def test_only_direct_children_of_parent(db_session, site):
    client = FakeSite({
        15: card("Проекты домов", "/addresses/projects/", None),
        16: card("Проект дома из кирпича", "/pr/kirpich/", 15),
        25: card("Полезные статьи", "/blog/", None),
        76: card("Проект без родителя", "/pr/orphan/", None),
        80: card("Вложенный", "/pr/deep/", 16),
    })
    present = sync_pages(db_session, site, client)
    assert [p.remote_id for p in present] == [16]
    assert 15 not in client.reads          # родитель сам себе не ребёнок
    page = pages_of(db_session, site)[16]
    assert (page.status, page.title, page.url) == ("new", "Проект дома из кирпича", "/pr/kirpich/")


def test_existing_page_keeps_status_and_gets_new_title(db_session, site):
    db_session.add(HouseProjectPage(site_id=site.id, remote_id=16, title="старое",
                                    status="done", rewrite_count=1))
    db_session.commit()
    sync_pages(db_session, site, FakeSite({16: card("новое", "/pr/k/", 15)}))
    page = pages_of(db_session, site)[16]
    assert (page.status, page.title, page.rewrite_count) == ("done", "новое", 1)


def test_missing_page_is_skipped_and_restored_when_back(db_session, site):
    db_session.add(HouseProjectPage(site_id=site.id, remote_id=16, status="new"))
    db_session.commit()
    sync_pages(db_session, site, FakeSite({17: card("другой", "/pr/d/", 15)}))
    page = pages_of(db_session, site)[16]
    assert (page.status, page.skip_reason) == ("skipped", NOT_ON_SITE)

    sync_pages(db_session, site, FakeSite({16: card("вернулся", "/pr/k/", 15)}))
    assert (page.status, page.skip_reason) == ("new", "")


def test_moved_to_other_parent_is_skipped(db_session, site):
    db_session.add(HouseProjectPage(site_id=site.id, remote_id=16, status="done"))
    db_session.commit()
    sync_pages(db_session, site, FakeSite({16: card("переехал", "/x/", 99)}))
    assert pages_of(db_session, site)[16].skip_reason == NOT_ON_SITE


def test_skipped_without_description_is_rechecked(db_session, site):
    from app.clock import utcnow

    db_session.add(HouseProjectPage(site_id=site.id, remote_id=16, status="skipped",
                                    skip_reason="нет блока описания", rewritten_at=utcnow()))
    db_session.commit()
    sync_pages(db_session, site, FakeSite({16: card("т", "/pr/k/", 15)}))
    assert pages_of(db_session, site)[16].status == "done"


def test_with_retries_repeats_network_and_5xx_only():
    calls, pauses = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise SiteAPIError("сеть", status_code=None)
        return "ok"

    assert with_retries(flaky, sleep=pauses.append) == "ok"
    assert pauses == [1, 2]

    def not_found():
        raise SiteAPIError("нет", status_code=404)

    with pytest.raises(SiteAPIError):
        with_retries(not_found, sleep=pauses.append)
    assert pauses == [1, 2]


def test_with_retries_gives_up_after_three():
    pauses = []

    def down():
        raise SiteAPIError("502", status_code=502)

    with pytest.raises(SiteAPIError):
        with_retries(down, sleep=pauses.append)
    assert pauses == [1, 2]
