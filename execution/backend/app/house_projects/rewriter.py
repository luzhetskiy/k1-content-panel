"""Рерайт описания одной страницы проекта дома.

Порядок: страница с сайта → оригинал (в первый раз — то, что на сайте; правка
руками после нас — новый оригинал) → LLM → проверка → страница перечитывается
и блок вставляется в свежую → PATCH → страница читается ещё раз, и наш текст
запоминается таким, каким его хранит сайт. На сайт уходит только текст,
прошедший проверку.

Зачем последнее чтение: свой текст на сайте панель узнаёт по `rewritten_html`.
Если запись прошла, а ответ не дошёл, или сайт хранит разметку иначе, чем мы её
отправили, наш же рерайт при следующем запуске выглядел бы ручной правкой и
тихо стал бы новым оригиналом."""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.ai.prompts import render_prompt, resolve_prompt
from app.clock import utcnow
from app.house_projects.markup import (
    clean_model_html, find_description, plain_text, replace_description, same_html,
)
from app.house_projects.sync import with_retries
from app.house_projects.validate import validate_rewrite
from app.models.house_project import HouseProjectPage
from app.sites.client import SiteAPIError

LLM_ATTEMPTS = 2     # первая попытка + одна с перечнем нарушений
NO_DESCRIPTION = "нет блока описания"


class RewriteError(RuntimeError):
    pass


def refresh_original(page: HouseProjectPage, current: str) -> None:
    """Повтор делается от оригинала. Но если описание на сайте не совпадает ни
    с оригиналом, ни с нашей последней версией, его поправили в админке — и
    правка становится новым оригиналом, иначе повтор затёр бы, например,
    исправленную площадь."""
    if not page.original_html:
        page.original_html = current
        return
    ours = bool(page.rewritten_html) and same_html(current, page.rewritten_html)
    if not ours and not same_html(current, page.original_html):
        page.original_html = current


def _read_block(site_client, page: HouseProjectPage, sleep):
    """(карточка страницы, её HTML, блок описания или None)."""
    detail = with_retries(lambda: site_client.get_page(page.remote_id), sleep)
    page_html = detail.get("text") or ""
    return detail, page_html, find_description(page_html)


def _stored_text(site_client, page: HouseProjectPage, sleep) -> str | None:
    """Описание, каким его сейчас хранит сайт; None — прочитать не удалось."""
    try:
        _, _, block = _read_block(site_client, page, sleep)
    except SiteAPIError:
        return None
    return block.inner_html.strip() if block else None


def rewrite_page(db: Session, page: HouseProjectPage, site, *, site_client, text_client,
                 record_usage, sleep=time.sleep) -> None:
    detail, _, block = _read_block(site_client, page, sleep)
    page.title = (detail.get("title") or page.title)[:300]
    page.url = (detail.get("url") or page.url)[:500]
    if block is None or not plain_text(block.inner_html):
        page.status, page.skip_reason, page.error_text = "skipped", NO_DESCRIPTION, ""
        page.updated_at = utcnow()
        db.commit()
        return
    current = block.inner_html.strip()
    refresh_original(page, current)
    db.commit()

    template = resolve_prompt(db, "project_rewrite", site.id)
    violations: list[str] = []
    for _ in range(LLM_ATTEMPTS):
        result = text_client.complete_text(render_prompt(template, {
            "site_name": site.name, "tone_of_voice": site.tone_of_voice, "title": page.title,
            "description_html": page.original_html,
            "char_count": len(plain_text(page.original_html)), "violations": violations,
        }), reasoning=False)
        record_usage(result.tokens_prompt, result.tokens_completion, result.cost)
        candidate = clean_model_html(result.text)
        violations = validate_rewrite(page.original_html, candidate)
        if not violations:
            break
    else:
        raise RewriteError("рерайт не прошёл проверку: " + "; ".join(violations))

    # Пока работала модель, страницу могли править в админке: вставляем блок в
    # свежую страницу, а если поправили само описание — не затираем правку.
    _, fresh_html, fresh_block = _read_block(site_client, page, sleep)
    if fresh_block is None or not same_html(fresh_block.inner_html, current):
        raise RewriteError("описание на сайте изменилось во время рерайта — "
                           "перепишите страницу ещё раз")
    new_html = replace_description(fresh_html, fresh_block, candidate)
    try:
        with_retries(lambda: site_client.update_page_text(page.remote_id, new_html,
                                                          as_is=True), sleep)
    except SiteAPIError as exc:
        # Сеть оборвалась после отправки — сайт мог текст принять: проверяем по факту.
        if exc.status_code is not None:
            raise
        stored = _stored_text(site_client, page, sleep)
        if stored is None or not same_html(stored, candidate):
            raise
    else:
        stored = _stored_text(site_client, page, sleep)
    now = utcnow()
    page.rewritten_html = stored or candidate
    page.rewritten_at = now
    page.rewrite_count += 1
    page.status, page.error_text, page.skip_reason = "done", "", ""
    page.updated_at = now
    db.commit()
