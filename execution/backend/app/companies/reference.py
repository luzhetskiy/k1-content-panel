"""Синхронизация карточки сайта с эталонной страницей строителя — по образцу
app/sites/reference.py для статей, но с проверкой разметочного контракта
вместо счётчика картинок.

Отличие от эталона статьи принципиальное: reference_html статей — просто
стилевой образец для RouterAI (LLM генерирует новую разметку по мотивам),
любой HTML подходит. builder_template_html — жёсткий контракт: fill_builder_
template (app/companies/template.py) ищет в шаблоне строго определённые
id/class и подставляет в них текст детерминированно, без LLM. Эталонная
страница обязана быть уже собрана этим же движком (или вручную по тому же
контракту) — иначе синхронизация молча сохранит шаблон, который не сможет
ничего заполнить.
"""

from __future__ import annotations

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models.site import Site
from app.sites.reference import ReferenceError

# Единственные элементы, без которых fill_builder_template не соберёт карточку
# вообще. Блоки лого/о компании/специализации/преимуществ там же штатно
# необязательны (см. fill_about() и logo_img/logo_text_span в template.py) —
# их отсутствие в эталоне не повод для отказа.
_REQUIRED_MARKERS = ("builder-main-title", "builder-contacts", "builder-contacts-grid")


def _missing_markers(html: str) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    missing = [marker for marker in _REQUIRED_MARKERS if soup.find(id=marker) is None]
    grid = soup.find(id="builder-contacts-grid")
    if grid is not None and grid.find(id="builder-contact-1") is None:
        missing.append("builder-contact-1")
    return missing


def sync_builder_reference(db: Session, site: Site, client, commit: bool = True) -> None:
    """Тянет эталонную карточку строителя, заполняет builder_template_html.
    `commit=False` — тот же приём, что sync_site_reference: вызывающий
    (sync_site в app/api/admin_sites.py) сам решает, когда коммитить."""
    if not site.builder_reference_id:
        raise ReferenceError("Эталонная карточка строителя не задана")

    reference = client.get_page(site.builder_reference_id)
    html = reference.get("text") or reference.get("body") or ""
    missing = _missing_markers(html)
    if missing:
        raise ReferenceError(
            "в эталонной странице нет обязательных элементов шаблона: "
            f"{', '.join(missing)} — это точно карточка компании, собранная "
            "этим сервисом?")

    site.builder_template_html = html
    site.builder_reference_synced_at = utcnow()
    if commit:
        db.commit()
