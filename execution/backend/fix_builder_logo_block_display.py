"""Разовая правка уже созданных страниц строителя: пустая запасная подпись
должна быть невидимой.

Атрибут hidden работает правилом [hidden]{display:none} из таблицы стилей
браузера, а любое авторское правило перебивает её независимо от
специфичности. У подписи класс h2, и в CSS сайтов есть
h2,.h2{display:block;line-height:28px;margin-bottom:25px} — из-за этого под
логотипом на каждой странице строителя оставалась пустая полоса.

Отдельный случай — 13 эталонных карточек: fix_builder_reference_logo_text
вставил в них живой span вообще без hidden, а эти страницы опубликованы.

Сборка новых страниц исправлена в app/companies/template.py (_hide/_show
проставляют и снимают инлайновый display:none), скрипт нужен только для
страниц, созданных до этого.

Запуск (из /app в контейнере api):
    python fix_builder_logo_block_display.py            # показать, что будет сделано
    python fix_builder_logo_block_display.py --apply    # записать на сайты
"""

from __future__ import annotations

import argparse

from bs4 import BeautifulSoup

from app.api.admin_sites import open_client
from app.companies.template import _hide
from app.db import SessionLocal
from app.models.company import Company
from app.models.site import Site


def hide_empty_logo_text(html: str) -> tuple[str, bool]:
    """Возвращает (html, был_ли_изменён). Правит только видимость и только
    у пустых элементов: подпись с текстом — это ручная вёрстка живой
    страницы (эталон stroybaza-moscow.ru показывает и логотип, и «ПИК»),
    трогать её нельзя."""
    soup = BeautifulSoup(html, "html.parser")
    changed = False
    for tag, is_empty in (
        (soup.find(id="builder-logo-text"), lambda t: not t.get_text(strip=True)),
        (soup.find(id="builder-logo"), lambda t: not (t.get("src") or "").strip()),
    ):
        if tag is None or not is_empty(tag):
            continue
        before = str(tag)
        _hide(tag)
        changed = changed or str(tag) != before
    return (str(soup), True) if changed else (html, False)


def _patch(client, page_id: int, label: str, apply: bool) -> bool:
    page = client.get_page(page_id)
    fixed, changed = hide_empty_logo_text(page.get("text") or "")
    if not changed:
        print(f"{label}: правка не требуется")
        return False
    if apply:
        client.update_page_text(page_id, fixed)
        print(f"{label}: страница {page_id} обновлена")
    else:
        print(f"{label}: страница {page_id} будет обновлена")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="записать изменения на сайты (без флага — только показать)")
    args = parser.parse_args()

    db = SessionLocal()
    clients: dict[int, object] = {}

    def client_for(site: Site):
        if site.id not in clients:
            clients[site.id] = open_client(db, site)
        return clients[site.id]

    touched = 0
    for site in db.query(Site).filter(Site.builder_reference_id.isnot(None)).all():
        touched += _patch(client_for(site), site.builder_reference_id,
                          f"эталон {site.name}", args.apply)

    companies = (db.query(Company)
                 .filter(Company.remote_page_id.isnot(None))
                 .order_by(Company.id).all())
    for company in companies:
        site = db.get(Site, company.site_id)
        if site is None:
            print(f"компания {company.id} {company.name}: сайт удалён — пропускаю")
            continue
        touched += _patch(client_for(site), company.remote_page_id,
                          f"компания {company.id} {company.name}", args.apply)

    print(f"\nитого страниц {'обновлено' if args.apply else 'к обновлению'}: {touched}")


if __name__ == "__main__":
    main()
