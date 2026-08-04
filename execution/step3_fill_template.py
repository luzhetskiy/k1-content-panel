"""
Шаг 3 (локальный): Генерация HTML-страниц компаний без API-ключа.
Заполняет шаблон builders.html данными из company_info напрямую через Python.

Запуск:
    python execution/step3_fill_template.py --target-site "https://vetonit-center.ru"
    python execution/step3_fill_template.py --target-site "https://vetonit-center.ru" --company-id 20
    python execution/step3_fill_template.py --target-site "https://vetonit-center.ru" --sphere "строительство частных домов" --region "Москва"
"""

import argparse
import json
import os
import re
import sys
from copy import copy
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

sys.path.insert(0, os.path.dirname(__file__))
import db
import filemanager

TEMPLATE_PATH = Path(__file__).parent.parent / "builders.html"


def localize_logo(logo_src: str, target_site: str, builder_name: str) -> str:
    """Скачивает внешний логотип и заливает его в /media/uploads/service-img/ на целевом
    сайте, возвращая локальный путь. Уже локальные пути (/media/...) и пустые — как есть."""
    if not logo_src or logo_src.startswith("/"):
        return logo_src
    try:
        local = filemanager.download_and_upload(
            target_site, logo_src, filemanager.SERVICE_IMG_DIR,
            filename_base=f"logo-{builder_name}",
        )
        print(f"      логотип → {local}")
        return local
    except Exception as e:
        print(f"      [WARN] логотип не локализован ({e}) — оставляем внешнюю ссылку")
        return logo_src


def load_template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def remove_comments(soup: BeautifulSoup) -> None:
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()


def set_text(tag, text: str) -> None:
    tag.clear()
    tag.append(NavigableString(text))


def fill_html(info: dict, template: str, target_site: str = "",
              localize_logos: bool = False) -> str:
    soup = BeautifulSoup(template, "html.parser")
    remove_comments(soup)

    name = (info.get("builder_name") or "").strip()
    city = (info.get("city_name") or "").strip()
    city_prep = (info.get("city_prepositional") or city).strip()
    label = (info.get("builder_label") or "").strip()
    logo_src = (info.get("builder_logo_src") or "").strip()
    if localize_logos and target_site:
        logo_src = localize_logo(logo_src, target_site, name)
    logo_alt = (info.get("builder_logo_alt") or name).strip()
    logo_svg = (info.get("builder_logo_svg") or "").strip()
    bg_src = (info.get("builder_background_src") or "").strip()
    short_desc = (info.get("builder_short_description") or "").strip()
    about = (info.get("about_company") or "").strip()
    spec = (info.get("specialization") or "").strip()
    projects = (info.get("projects_services") or "").strip()
    benefits = (info.get("benefits") or "").strip()

    contacts = info.get("contacts") or []
    if isinstance(contacts, str):
        try:
            contacts = json.loads(contacts)
        except Exception:
            contacts = []
    if not contacts:
        contacts = [{}]

    # ── главный блок ──────────────────────────────────────────────
    builder_div = soup.find(id="builder")
    if builder_div:
        builder_div["data-builder-name"] = name
        builder_div["data-builder-city"] = city

    # лейбл — статичен в шаблоне, не трогаем

    # логотип: img → svg → текстовый fallback
    logo_img = soup.find(id="builder-logo")
    logo_text_span = soup.find(id="builder-logo-text")
    if logo_src:
        # Вариант А: img-логотип
        if logo_img:
            logo_img["src"] = logo_src
            logo_img["alt"] = logo_alt
        if logo_text_span:
            logo_text_span.decompose()
    elif logo_svg:
        # Вариант Б: SVG-логотип — вставляем разметку вместо img
        if logo_img:
            svg_soup = BeautifulSoup(logo_svg, "html.parser")
            svg_tag = svg_soup.find("svg")
            if svg_tag:
                svg_tag["class"] = list(logo_img.get("class", [])) or \
                    ["partner-logo", "builder-logo"]
                svg_tag["id"] = "builder-logo"
                svg_tag["role"] = "img"
                svg_tag["aria-label"] = f"Логотип {name}"
                logo_img.replace_with(svg_tag)
            else:
                logo_img.decompose()
        if logo_text_span:
            logo_text_span.decompose()
    else:
        # Вариант В: текстовый fallback
        if logo_img:
            logo_img.decompose()
        if logo_text_span:
            set_text(logo_text_span, name)

    # фон — статичен в шаблоне, не трогаем

    # ── заголовок ─────────────────────────────────────────────────
    main_title = soup.find(id="builder-main-title")
    if main_title:
        set_text(main_title, f"О компании {name}")

    # ── about-блоки ───────────────────────────────────────────────
    def fill_about(block_id: str, text: str) -> None:
        block = soup.find(id=block_id)
        if not block:
            return
        if text:
            p = block.find("p")
            if p:
                p.clear()
                for i, para in enumerate(text.split("\n\n")):
                    para = para.strip()
                    if not para:
                        continue
                    if i == 0:
                        p.append(NavigableString(para))
                    else:
                        new_p = soup.new_tag("p")
                        new_p.append(NavigableString(para))
                        block.append(new_p)
        else:
            block.decompose()

    fill_about("builder-about-company", about)
    fill_about("builder-specialization", spec)
    fill_about("builder-projects-services", projects)
    fill_about("builder-benefits", benefits)

    # ── контакты ──────────────────────────────────────────────────
    contacts_div = soup.find(id="builder-contacts")
    if contacts_div:
        title = contacts_div.find(id="builder-contacts-title")
        if title:
            # city_prepositional может уже содержать предлог "в Москве" или только "Москве"
            prep = city_prep
            if prep.lower().startswith(("в ", "во ")):
                set_text(title, f"{name} {prep}")
            else:
                set_text(title, f"{name} в {prep}")

        grid = contacts_div.find(id="builder-contacts-grid")
        if grid:
            tpl_item = grid.find("div", id="builder-contact-1")

            def rebuild_anchor(el, href: str, text: str) -> None:
                """Очищает <a>, ставит href, оставляет circle-img, добавляет текст."""
                el["href"] = href
                circle = el.find(class_="circle-img")
                circle_copy = BeautifulSoup(str(circle), "html.parser") if circle else None
                el.clear()
                if circle_copy:
                    el.append(circle_copy)
                el.append(NavigableString(f"\n        {text}\n      "))

            items_html = []
            for idx, c in enumerate(contacts, start=1):
                addr = (c.get("address") or "").strip()
                phone_tel = (c.get("phone_tel") or "").strip()
                phone_text_val = (c.get("phone_text") or phone_tel).strip()
                email_val = (c.get("email") or "").strip()
                hours = (c.get("working_hours") or "").strip()
                site_url = (c.get("site_url") or "").strip()
                site_text_val = (c.get("site_text") or site_url).strip()
                note = (c.get("note") or "").strip()

                if not any([addr, phone_tel, email_val, hours, site_url]):
                    continue

                item = BeautifulSoup(str(tpl_item), "html.parser")
                item_div = item.find("div")
                item_div["id"] = f"builder-contact-{idx}"
                item_div["data-contact-city"] = city

                def line(cls_fragment, keep: bool, mutate=None):
                    el = item_div.find(class_=lambda c: c and cls_fragment in c)
                    if el:
                        if keep and mutate:
                            mutate(el)
                        elif not keep:
                            el.decompose()

                line("builder-line-address", bool(addr),
                     lambda el: set_text(el.find("p"), addr) if el.find("p") else None)
                line("builder-line-phone", bool(phone_tel),
                     lambda el: rebuild_anchor(el, f"tel:{phone_tel}", phone_text_val))
                line("builder-line-email", bool(email_val),
                     lambda el: rebuild_anchor(el, f"mailto:{email_val}", email_val))
                line("builder-line-time", bool(hours),
                     lambda el: set_text(el.find("p"), hours) if el.find("p") else None)
                line("builder-line-site", bool(site_url),
                     lambda el: rebuild_anchor(el, site_url, site_text_val))
                line("builder-line-note", bool(note),
                     lambda el: set_text(el.find("p"), note) if el.find("p") else None)

                items_html.append(str(item_div))

            grid.clear()
            for h in items_html:
                grid.append(BeautifulSoup(h, "html.parser"))

    return str(soup)


def process_company(company_id: int, target_site: str, force: bool = False,
                    localize_logos: bool = False) -> bool:
    existing = db.get_generated_content(company_id, target_site)
    if existing and not force:
        print(f"  [{company_id}] уже есть — пропускаем (--force для перегенерации)")
        return True

    info = db.get_company_info(company_id)
    if not info:
        print(f"  [{company_id}] нет данных в company_info — пропускаем")
        return False

    template = load_template()
    html = fill_html(info, template, target_site, localize_logos)
    db.save_generated_content(company_id, target_site, html)
    name = info.get("builder_name") or "?"
    print(f"  [{company_id}] {name} — OK ({len(html)} симв.)")
    return True


def main():
    parser = argparse.ArgumentParser(description="Генерация HTML без API-ключа")
    parser.add_argument("--target-site", required=True, help="URL целевого сайта")
    parser.add_argument("--company-id", type=int, help="ID одной компании")
    parser.add_argument("--sphere", help="Фильтр по сфере")
    parser.add_argument("--region", help="Фильтр по региону")
    parser.add_argument("--force", action="store_true", help="Перегенерировать существующие")
    parser.add_argument("--localize-logos", action="store_true",
                        help="Скачивать внешние логотипы и заливать в /media/uploads/service-img/ целевого сайта")
    args = parser.parse_args()

    db.init_db()

    if args.company_id:
        company_ids = [args.company_id]
    else:
        companies = db.get_companies(
            region=args.region,
            sphere=args.sphere,
            limit=1000,
        )
        company_ids = [c["id"] for c in companies]

    target = args.target_site.rstrip("/")
    print(f"\nЦелевой сайт: {target}")
    print(f"Компаний к обработке: {len(company_ids)}")
    print("-" * 60)

    ok = sum(process_company(cid, target, args.force, args.localize_logos)
             for cid in company_ids)
    print(f"\nГотово: {ok}/{len(company_ids)}")


if __name__ == "__main__":
    main()
