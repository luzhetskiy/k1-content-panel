from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.category_meta.tree import (
    SKIP_DELETED, SKIP_NO_PAGE, SKIP_UNPUBLISHED, category_path, category_url, sync_categories,
)
from app.models.category_meta import CategoryMeta
from app.models.site import Site
from app.sites.client import SiteAPIError

CATEGORIES = [
    {"id": 45, "name": "Листовые материалы", "slug": "listovye-materialy", "parent": None,
     "published": True},
    {"id": 46, "name": "Фанера", "slug": "fanera", "parent": 45, "published": True},
    {"id": 60, "name": "Крепеж", "slug": "krepezh", "parent": None, "published": True},
    {"id": 61, "name": "Механические анкеры", "slug": "mehanicheskie-ankery", "parent": 60,
     "published": True},
    {"id": 62, "name": "Анкер клиновой", "slug": "anker-klinovoj", "parent": 61, "published": True},
    {"id": 70, "name": "Водосток Docke", "slug": "vodostok-docke", "parent": None,
     "published": False},
    {"id": 71, "name": "Сэндвич-панели", "slug": "sendvich-paneli", "parent": 60, "published": True},
]
SITEMAP = {"/catalog/listovye-materialy/", "/catalog/category/listovye-materialy/fanera/",
           "/catalog/krepezh/", "/catalog/category/krepezh/mehanicheskie-ankery/",
           "/catalog/category/mehanicheskie-ankery/anker-klinovoj/"}


@pytest.fixture
def site(db_session):
    row = Site(name="С", domain="s.ru", base_url="https://s.ru", api_token_enc="e")
    db_session.add(row)
    db_session.commit()
    return row


def fake_client(categories=CATEGORIES, sitemap=SITEMAP):
    def fetch_sitemap_paths():
        if isinstance(sitemap, Exception):
            raise sitemap
        return sitemap
    return SimpleNamespace(list_catalog_categories=lambda: categories,
                           fetch_sitemap_paths=fetch_sitemap_paths)


def rows(db, site):
    return {r.remote_id: r for r in db.scalars(
        select(CategoryMeta).where(CategoryMeta.site_id == site.id)).all()}


def test_url_rule_root_and_nested():
    by_id = {c["id"]: c for c in CATEGORIES}
    assert category_url(by_id[45], by_id) == "/catalog/listovye-materialy/"
    assert category_url(by_id[46], by_id) == "/catalog/category/listovye-materialy/fanera/"
    # третий уровень — slug НЕПОСРЕДСТВЕННОГО родителя, а не корня
    assert category_url(by_id[62], by_id) == "/catalog/category/mehanicheskie-ankery/anker-klinovoj/"
    assert category_url({"id": 9, "slug": "x", "parent": 999}, by_id) == ""


def test_path_from_root():
    by_id = {c["id"]: c for c in CATEGORIES}
    assert category_path(by_id[62], by_id) == "Крепеж / Механические анкеры / Анкер клиновой"


def test_sync_creates_rows_and_skips(db_session, site):
    result = sync_categories(db_session, site, fake_client())
    by_remote = rows(db_session, site)
    assert {r.remote_id for r in result.active} == {45, 46, 60, 61, 62}
    assert result.skipped == 2 and result.sitemap_error == ""
    assert by_remote[46].status == "new"
    assert by_remote[46].url == "/catalog/category/listovye-materialy/fanera/"
    assert by_remote[46].remote_parent_id == 45
    assert (by_remote[70].status, by_remote[70].skip_reason) == ("skipped", SKIP_UNPUBLISHED)
    assert (by_remote[71].status, by_remote[71].skip_reason) == ("skipped", SKIP_NO_PAGE)


def test_sync_keeps_existing_status_and_tags(db_session, site):
    sync_categories(db_session, site, fake_client())
    fanera = rows(db_session, site)[46]
    fanera.status, fanera.title = "done", "Фанера в Москве | Стройбаза"
    db_session.commit()
    renamed = [dict(c, name="Фанера строительная") if c["id"] == 46 else c for c in CATEGORIES]
    sync_categories(db_session, site, fake_client(renamed))
    fanera = rows(db_session, site)[46]
    assert fanera.status == "done"
    assert fanera.title == "Фанера в Москве | Стройбаза"
    assert fanera.name == "Фанера строительная"


def test_sync_marks_deleted_and_revives(db_session, site):
    sync_categories(db_session, site, fake_client())
    without_fanera = [c for c in CATEGORIES if c["id"] != 46]
    sync_categories(db_session, site, fake_client(without_fanera))
    assert (rows(db_session, site)[46].status, rows(db_session, site)[46].skip_reason) == \
        ("skipped", SKIP_DELETED)
    sync_categories(db_session, site, fake_client())
    assert (rows(db_session, site)[46].status, rows(db_session, site)[46].skip_reason) == ("new", "")


def test_sitemap_failure_does_not_skip(db_session, site):
    result = sync_categories(db_session, site, fake_client(sitemap=SiteAPIError("sitemap: 503")))
    assert {r.remote_id for r in result.active} == {45, 46, 60, 61, 62, 71}
    assert result.sitemap_error == "sitemap: 503"
