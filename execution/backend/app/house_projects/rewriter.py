"""Рерайт описания одной страницы проекта дома.

Порядок: свежая страница с сайта → оригинал (в первый раз — то, что на сайте;
правка руками после нас — новый оригинал) → LLM → проверка → вставка блока в
только что прочитанную страницу → PATCH. На сайт уходит только текст,
прошедший проверку."""

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


def rewrite_page(db: Session, page: HouseProjectPage, site, *, site_client, text_client,
                 record_usage, sleep=time.sleep) -> None:
    detail = with_retries(lambda: site_client.get_page(page.remote_id), sleep)
    page_html = detail.get("text") or ""
    page.title = (detail.get("title") or page.title)[:300]
    page.url = (detail.get("url") or page.url)[:500]
    block = find_description(page_html)
    if block is None:
        page.status, page.skip_reason, page.error_text = "skipped", NO_DESCRIPTION, ""
        page.updated_at = utcnow()
        db.commit()
        return
    refresh_original(page, block.inner_html.strip())
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

    new_html = replace_description(page_html, block, candidate)
    with_retries(lambda: site_client.update_page_text(page.remote_id, new_html, as_is=True),
                 sleep)
    now = utcnow()
    page.rewritten_html = candidate
    page.rewritten_at = now
    page.rewrite_count += 1
    page.status, page.error_text, page.skip_reason = "done", "", ""
    page.updated_at = now
    db.commit()
