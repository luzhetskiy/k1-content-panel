#!/usr/bin/env python3
"""
Шаг 3 (ручной): Заполняем builders.html данными из company_info.
Без API-ключа — подстановка выполняется прямо в скрипте.
"""
import json
import re
import sqlite3
from pathlib import Path
from bs4 import BeautifulSoup, Comment

TEMPLATE_PATH = Path(__file__).parent.parent / "builders.html"
DB_PATH = Path(__file__).parent / "data" / "companies.db"
TARGET_SITE = "https://stroybaza-samara.ru"
DEFAULT_BG = "/media/uploads/stroiteli/builder-back.webp"


def fill_template(template_html: str, info: dict, c1: dict) -> str:
    builder_name = info.get("builder_name") or info.get("name") or ""
    city_name = info.get("city_name") or "Самара"
    city_prepositional = info.get("city_prepositional") or "Самаре"
    logo_src = info.get("builder_logo_src") or ""
    logo_alt = info.get("builder_logo_alt") or ""
    bg_src = info.get("builder_background_src") or DEFAULT_BG
    label_text = info.get("builder_label") or ""
    short_desc = info.get("builder_short_description") or ""

    soup = BeautifulSoup(template_html, "html.parser")

    # Удаляем HTML-комментарии
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # data-* на главном блоке
    builder_div = soup.find(id="builder")
    if builder_div:
        builder_div["data-builder-name"] = builder_name
        builder_div["data-builder-city"] = city_name

    # Лейбл
    label_span = soup.find(id="builder-label")
    if label_span:
        if label_text:
            label_span.clear()
            label_span.append(label_text)
        else:
            label_span.decompose()

    # Логотип vs текстовый fallback
    logo_img = soup.find(id="builder-logo")
    logo_text_span = soup.find(id="builder-logo-text")
    if logo_src:
        if logo_img:
            logo_img["src"] = logo_src
            logo_img["alt"] = logo_alt or f"Логотип {builder_name}"
        if logo_text_span:
            logo_text_span.decompose()
    else:
        if logo_img:
            logo_img.decompose()
        if logo_text_span:
            logo_text_span.clear()
            text = f"{builder_name} — {short_desc}" if short_desc else builder_name
            logo_text_span.append(text)

    # Фоновое изображение
    bg_img = soup.find(id="builder-background")
    if bg_img:
        bg_img["src"] = bg_src

    # H2-заголовок
    title_h2 = soup.find(id="builder-main-title")
    if title_h2:
        title_h2.clear()
        title_h2.append(info.get("builder_main_title") or "")

    # Текстовые блоки «О компании»
    def set_section(elem_id: str, text: str) -> None:
        el = soup.find(id=elem_id)
        if el:
            p = el.find("p")
            if p:
                p.clear()
                p.append(text or "")

    set_section("builder-about-company", info.get("about_company") or "")
    set_section("builder-specialization", info.get("specialization") or "")
    set_section("builder-projects-services", info.get("projects_services") or "")
    set_section("builder-benefits", info.get("benefits") or "")

    # Заголовок блока контактов
    contacts_title = soup.find(id="builder-contacts-title")
    if contacts_title:
        contacts_title.clear()
        contacts_title.append(f"{builder_name} в {city_prepositional}")

    # Контакт 1
    contact_div = soup.find(id="builder-contact-1")
    if contact_div:
        contact_div["data-contact-city"] = city_name

        def handle_contact_block(selector, elem_type, field_val, href=None, text_val=None):
            el = contact_div.find(class_=selector)
            if not el:
                return
            if field_val:
                if href:
                    el["href"] = href
                circle = el.find(class_="circle-img")
                for child in list(el.children):
                    if hasattr(child, 'name') and child.name is None and str(child).strip():
                        child.extract()
                display = text_val or field_val
                if elem_type in ("a",):
                    if circle:
                        circle.insert_after(f" {display} ")
                    else:
                        el.append(display)
                else:
                    p = el.find("p")
                    if p:
                        p.clear()
                        p.append(display)
            else:
                el.decompose()

        address = c1.get("address") or ""
        phone_tel = c1.get("phone_tel") or ""
        phone_text = c1.get("phone_text") or ""
        email = c1.get("email") or ""
        hours = c1.get("working_hours") or ""
        site_url = c1.get("site_url") or ""
        site_text = c1.get("site_text") or ""
        note = c1.get("note") or ""

        handle_contact_block("builder-line-address", "div", address)
        handle_contact_block("builder-line-phone", "a", phone_tel,
                             href=f"tel:{phone_tel}", text_val=phone_text)
        handle_contact_block("builder-line-email", "a", email,
                             href=f"mailto:{email}", text_val=email)
        handle_contact_block("builder-line-time", "div", hours)
        handle_contact_block("builder-line-site", "a", site_url,
                             href=site_url, text_val=site_text or site_url)
        handle_contact_block("builder-line-note", "div", note)

    result = str(soup)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def main():
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    rows = conn.execute("""
        SELECT c.id, c.name,
               ci.builder_name, ci.city_name, ci.city_prepositional,
               ci.builder_label, ci.builder_logo_src, ci.builder_logo_alt,
               ci.builder_background_src, ci.builder_short_description,
               ci.builder_main_title, ci.about_company, ci.specialization,
               ci.projects_services, ci.benefits, ci.contacts
        FROM companies c JOIN company_info ci ON c.id=ci.company_id
        ORDER BY c.id
    """).fetchall()

    print(f"Генерирую для {len(rows)} компаний → {TARGET_SITE}")
    print("-" * 60)

    success = 0
    for row in rows:
        info = dict(row)
        cid = info["id"]

        contacts = []
        if info.get("contacts"):
            try:
                contacts = json.loads(info["contacts"])
            except Exception:
                pass
        c1 = contacts[0] if contacts else {}

        html = fill_template(template, info, c1)

        conn.execute("""
            INSERT INTO generated_content (company_id, target_site, html_content)
            VALUES (?, ?, ?)
            ON CONFLICT(company_id, target_site) DO UPDATE SET
                html_content=excluded.html_content,
                verified=0,
                verification_notes=NULL,
                created_at=datetime('now')
        """, (cid, TARGET_SITE, html))
        conn.commit()

        name_display = info.get("builder_name") or info.get("name") or ""
        print(f"  ✓ id={cid:2d}  {name_display:<30}  ({len(html):,} chars)")
        success += 1

    print(f"\nГотово: {success}/{len(rows)}")
    conn.close()


if __name__ == "__main__":
    main()
