"""Синхронизация карточки сайта с самим сайтом: раздел статей и эталонная статья.

Эталон — единственный источник разметки, отдельного HTML-шаблона в сервисе нет.
Он кешируется в карточке: при генерации статьи за ним на сайт не ходим.

Кеш экономит один HTTP-запрос на статью — это мелочь. Основная цена эталона в том,
что его HTML уходит во входные токены КАЖДОГО запроса article_body, и кеширование
этого не меняет. Если счёт за текст станет заметным, сокращать надо объём эталона
(скелет разметки вместо полного HTML), а не число обращений.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models.site import Site

# Комментарии вырезаются перед подсчётом: закомментированная картинка не
# рендерится, а лишний сгенерированный кадр стоит денег.
_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
_IMG = re.compile(r"<img\b", re.IGNORECASE)


class ReferenceError(RuntimeError):
    pass


def count_images(html: str) -> int:
    """Сколько <img> в разметке — столько картинок и генерируется для статьи."""
    return len(_IMG.findall(_COMMENTS.sub("", html or "")))


def sync_site_reference(db: Session, site: Site, client, commit: bool = True) -> None:
    """Тянет раздел и эталон, заполняет кеш карточки. Бросает ReferenceError
    с человеческим текстом — вызывающий показывает его администратору.

    `commit=False` — для вызывающих, которым нужен один коммит на несколько
    шагов (`sync_site` в app/api/admin_sites.py: эталон и список страниц
    раздела пишутся одной транзакцией, чтобы отказ на втором шаге не оставлял
    в БД наполовину обновлённую карточку). По умолчанию коммитит сама — так
    же, как `SettingsService.set`/`set_secret` (app/settings/service.py)."""
    if not site.articles_parent_id:
        raise ReferenceError("не задан id родительской страницы раздела статей")
    if not site.reference_article_id:
        raise ReferenceError("Эталонная статья не задана — без неё разметку взять неоткуда")

    parent = client.get_page(site.articles_parent_id)
    prefix = parent.get("url") or ""
    if not prefix:
        raise ReferenceError(f"у страницы {site.articles_parent_id} нет url — "
                             f"это точно раздел статей?")

    reference = client.get_page(site.reference_article_id)
    html = reference.get("text") or reference.get("body") or ""
    images = count_images(html)
    if images == 0:
        raise ReferenceError("в эталонной статье ни одной картинки — статьи получатся "
                             "без иллюстраций; проверь, тот ли это id")

    site.articles_url_prefix = prefix if prefix.endswith("/") else prefix + "/"
    site.reference_html = html
    site.reference_images = images
    site.reference_synced_at = utcnow()
    if commit:
        db.commit()
