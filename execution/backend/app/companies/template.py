"""Заполнение builder_template_html данными компании. Портирует
fill_html из execution/step3_fill_template.py — та же разметка-контракт
(id/класс атрибуты шаблона), без локализации логотипа."""

from __future__ import annotations

import json
import logging

from bs4 import BeautifulSoup, Comment, NavigableString

logger = logging.getLogger(__name__)


def _remove_comments(soup: BeautifulSoup) -> None:
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()


def _set_text(tag, text: str) -> None:
    tag.clear()
    tag.append(NavigableString(text))


_DISPLAY_NONE = "display:none"


def _declarations(tag) -> list[str]:
    return [d.strip() for d in (tag.get("style") or "").split(";") if d.strip()]


def _set_style(tag, declarations: list[str]) -> None:
    if declarations:
        tag["style"] = ";".join(declarations)
    elif "style" in tag.attrs:
        del tag["style"]


def _without_display(declarations: list[str]) -> list[str]:
    return [d for d in declarations if d.split(":", 1)[0].strip().lower() != "display"]


def _hide(tag) -> None:
    """Лишний элемент блока логотипа скрывается, а не удаляется: шаблон
    сайта синхронизируется с уже собранной страницы (app/companies/
    reference.py), поэтому удалённый элемент исчез бы из шаблона навсегда —
    и следующая компания осталась бы либо без картинки, либо без подписи."""
    tag["hidden"] = ""
    # Одного атрибута мало: он работает правилом [hidden]{display:none} из
    # таблицы стилей браузера, а любое авторское правило перебивает её
    # независимо от специфичности. У запасной подписи класс h2, и в CSS
    # сайтов есть h2,.h2{display:block;line-height:28px;margin-bottom:25px} —
    # из-за этого скрытая пустая подпись занимала полосу под логотипом на
    # каждой странице строителя. Инлайновый стиль не перебивает ничто, кроме
    # !important.
    _set_style(tag, _without_display(_declarations(tag)) + [_DISPLAY_NONE])
    if tag.name == "img" and "src" in tag.attrs:
        # без src скрытая картинка не делает лишнего запроса; пустой src
        # (как в шаблоне по умолчанию) сохранять в data-src незачем
        if tag["src"]:
            tag["data-src"] = tag["src"]
        del tag["src"]


def _show(tag) -> None:
    """Обратная _hide операция. Снять инлайновый display:none так же важно,
    как и атрибут: в шаблон элемент приезжает с уже собранной страницы, где
    прошлая компания его скрыла, — без снятия логотип следующей компании был
    бы невидим."""
    if "hidden" in tag.attrs:
        del tag["hidden"]
    _set_style(tag, _without_display(_declarations(tag)))


def fill_builder_template(template: str, info: dict) -> str:
    soup = BeautifulSoup(template, "html.parser")
    _remove_comments(soup)

    name = (info.get("builder_name") or "").strip()
    city_name = (info.get("city_name") or "").strip()
    city_prep = (info.get("city_prepositional") or info.get("city_name") or "").strip()

    root = soup.find(id="builder")
    if root:
        root["data-builder-name"] = name
        root["data-builder-city"] = city_name
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
            logger.warning("не удалось разобрать contacts как JSON: %r", contacts[:200])
            contacts = []
    if not isinstance(contacts, list):
        logger.warning("contacts не список (%s) — контакты не будут показаны",
                       type(contacts).__name__)
        contacts = []
    if not contacts:
        contacts = [{}]

    logo_img = soup.find(id="builder-logo")
    logo_text_span = soup.find(id="builder-logo-text")
    if logo_src:
        if logo_img:
            logo_img["src"] = logo_src
            logo_img["alt"] = logo_alt
            _show(logo_img)
        if logo_text_span:
            _set_text(logo_text_span, "")
            _hide(logo_text_span)
    else:
        if logo_img:
            _hide(logo_img)
        if logo_text_span:
            _set_text(logo_text_span, name)
            _show(logo_text_span)

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
