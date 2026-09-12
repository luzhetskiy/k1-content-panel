"""Разовая правка эталонных страниц строителя: запасная подпись
(builder-logo-text) должна быть живой разметкой, а не комментарием.

На проде 10.09.2026 живого элемента не было ни у одного из 13 сайтов: у 9 он
лежал внутри HTML-комментария, у 4 отсутствовал. fill_builder_template
вырезает комментарии перед заполнением, поэтому компания без логотипа
оставалась и без картинки, и без названия.

Запуск (из /app в контейнере api):
    python fix_builder_reference_logo_text.py            # показать, что будет сделано
    python fix_builder_reference_logo_text.py --apply    # записать на сайты
"""

from __future__ import annotations

import argparse
import re

from bs4 import BeautifulSoup, Comment

from app.api.admin_sites import open_client
from app.db import SessionLocal
from app.models.site import Site

_SPAN_MARKUP = '<span class="h2 builder-logo-text" id="builder-logo-text"></span>'
_COMMENTED_SPAN = re.compile(r'<span[^>]*id="builder-logo-text".*?</span>', re.S)


def ensure_logo_text(html: str) -> tuple[str, bool]:
    """Возвращает (html, был_ли_изменён). Страницу без builder-logo не трогает."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.find(id="builder-logo-text") is not None:
        return html, False
    logo_img = soup.find(id="builder-logo")
    if logo_img is None:
        return html, False

    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        match = _COMMENTED_SPAN.search(str(comment))
        if match:
            comment.replace_with(BeautifulSoup(match.group(0), "html.parser"))
            return str(soup), True

    logo_img.insert_after(BeautifulSoup(_SPAN_MARKUP, "html.parser"))
    return str(soup), True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="записать изменения на сайты (без флага — только показать)")
    args = parser.parse_args()

    db = SessionLocal()
    for site in db.query(Site).filter(Site.builder_reference_id.isnot(None)).all():
        client = open_client(db, site)
        page = client.get_page(site.builder_reference_id)
        html = page.get("text") or page.get("body") or ""
        fixed, changed = ensure_logo_text(html)
        if not changed:
            print(f"{site.name}: правка не требуется или нет builder-logo — пропускаю")
            continue
        if args.apply:
            client.update_page_text(site.builder_reference_id, fixed)
            print(f"{site.name}: эталон {site.builder_reference_id} обновлён")
        else:
            print(f"{site.name}: эталон {site.builder_reference_id} будет обновлён")


if __name__ == "__main__":
    main()
