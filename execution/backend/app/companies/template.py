"""Заполнение builder_template_html данными компании. Портирует
fill_html из execution/step3_fill_template.py — та же разметка-контракт
(id/класс атрибуты шаблона), без локализации логотипа."""

from __future__ import annotations

import json

from bs4 import BeautifulSoup, Comment, NavigableString


def _remove_comments(soup: BeautifulSoup) -> None:
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()


def _set_text(tag, text: str) -> None:
    tag.clear()
    tag.append(NavigableString(text))


def fill_builder_template(template: str, info: dict) -> str:
    soup = BeautifulSoup(template, "html.parser")
    _remove_comments(soup)

    name = (info.get("builder_name") or "").strip()
    city_prep = (info.get("city_prepositional") or info.get("city_name") or "").strip()
    logo_src = (info.get("builder_logo_src") or "").strip()
    logo_alt = (info.get("builder_logo_alt") or name).strip()
    about = (info.get("about_company") or "").strip()
    spec = (info.get("specialization") or "").strip()
    projects = (info.get("projects_services") or "").strip()
    benefits = (info.get("benefits") or "").strip()

    contacts = info.get("contacts") or []
    if isinstance(contacts, str):
        try:
            contacts = json.loads(contacts)
        except json.JSONDecodeError:
            contacts = []
    if not contacts:
        contacts = [{}]

    logo_img = soup.find(id="builder-logo")
    logo_text_span = soup.find(id="builder-logo-text")
    if logo_src:
        if logo_img:
            logo_img["src"] = logo_src
            logo_img["alt"] = logo_alt
        if logo_text_span:
            logo_text_span.decompose()
    else:
        if logo_img:
            logo_img.decompose()
        if logo_text_span:
            _set_text(logo_text_span, name)

    main_title = soup.find(id="builder-main-title")
    if main_title:
        _set_text(main_title, f"О компании {name}")

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

    contacts_div = soup.find(id="builder-contacts")
    if contacts_div:
        title = contacts_div.find(id="builder-contacts-title")
        if title:
            prep = city_prep
            if prep.lower().startswith(("в ", "во ")):
                _set_text(title, f"{name} {prep}")
            else:
                _set_text(title, f"{name} в {prep}")

        grid = contacts_div.find(id="builder-contacts-grid")
        if grid:
            tpl_item = grid.find("div", id="builder-contact-1")

            def rebuild_anchor(el, href: str, text: str) -> None:
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
                if tpl_item is None:
                    continue

                item = BeautifulSoup(str(tpl_item), "html.parser")
                item_div = item.find("div")
                item_div["id"] = f"builder-contact-{idx}"

                def line(cls_fragment, keep, mutate=None):
                    el = item_div.find(class_=lambda c: c and cls_fragment in c)
                    if el:
                        if keep and mutate:
                            mutate(el)
                        elif not keep:
                            el.decompose()

                line("builder-line-address", bool(addr),
                    lambda el: _set_text(el.find("p"), addr) if el.find("p") else None)
                line("builder-line-phone", bool(phone_tel),
                    lambda el: rebuild_anchor(el, f"tel:{phone_tel}", phone_text_val))
                line("builder-line-email", bool(email_val),
                    lambda el: rebuild_anchor(el, f"mailto:{email_val}", email_val))
                line("builder-line-time", bool(hours),
                    lambda el: _set_text(el.find("p"), hours) if el.find("p") else None)
                line("builder-line-site", bool(site_url),
                    lambda el: rebuild_anchor(el, site_url, site_text_val))
                line("builder-line-note", bool(note),
                    lambda el: _set_text(el.find("p"), note) if el.find("p") else None)

                items_html.append(str(item_div))

            grid.clear()
            for h in items_html:
                grid.append(BeautifulSoup(h, "html.parser"))

    return str(soup)
