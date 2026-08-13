import pytest

from app.companies.reference import sync_builder_reference
from app.sites.reference import ReferenceError

_VALID_TEMPLATE = (
    '<div id="builder">'
    '<h1 id="builder-main-title"></h1>'
    '<div id="builder-contacts">'
    '<div id="builder-contacts-grid">'
    '<div id="builder-contact-1"></div>'
    '</div></div></div>'
)


class FakeClient:
    def __init__(self, html=_VALID_TEMPLATE):
        self.html = html
        self.requested = []

    def get_page(self, page_id):
        self.requested.append(page_id)
        return {"id": page_id, "text": self.html}


@pytest.fixture
def site(db_session):
    from app.models.site import Site

    row = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
               builder_reference_id=77)
    db_session.add(row)
    db_session.commit()
    return row


def test_sync_caches_template_html(db_session, site):
    sync_builder_reference(db_session, site, FakeClient())
    assert "builder-main-title" in site.builder_template_html
    assert site.builder_reference_synced_at is not None


def test_sync_requires_reference_id(db_session, site):
    site.builder_reference_id = None
    db_session.commit()
    with pytest.raises(ReferenceError, match="Эталонная"):
        sync_builder_reference(db_session, site, FakeClient())


def test_sync_rejects_page_missing_main_title(db_session, site):
    html = _VALID_TEMPLATE.replace('id="builder-main-title"', 'id="something-else"')
    with pytest.raises(ReferenceError, match="builder-main-title"):
        sync_builder_reference(db_session, site, FakeClient(html=html))


def test_sync_rejects_page_missing_contacts_grid(db_session, site):
    html = '<div id="builder"><h1 id="builder-main-title"></h1></div>'
    with pytest.raises(ReferenceError, match="builder-contacts"):
        sync_builder_reference(db_session, site, FakeClient(html=html))


def test_sync_rejects_page_missing_contact_template_item(db_session, site):
    html = ('<div id="builder"><h1 id="builder-main-title"></h1>'
            '<div id="builder-contacts"><div id="builder-contacts-grid"></div></div></div>')
    with pytest.raises(ReferenceError, match="builder-contact-1"):
        sync_builder_reference(db_session, site, FakeClient(html=html))


def test_sync_failure_does_not_clobber_previous_cache(db_session, site):
    """Отказ повторной синхронизации не должен стирать кеш от прошлой
    успешной — иначе один плохой запуск оставляет сайт без шаблона вовсе."""
    sync_builder_reference(db_session, site, FakeClient())
    old_html = site.builder_template_html
    old_synced_at = site.builder_reference_synced_at

    with pytest.raises(ReferenceError):
        sync_builder_reference(db_session, site, FakeClient(html="<p>плохой</p>"))

    assert site.builder_template_html == old_html
    assert site.builder_reference_synced_at == old_synced_at
