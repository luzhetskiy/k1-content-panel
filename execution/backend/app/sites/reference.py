"""Синхронизация карточки сайта с самим сайтом: раздел статей и эталонная статья.

Эталон — единственный источник разметки, отдельного HTML-шаблона в сервисе нет.
Он кешируется в карточке: при генерации статьи за ним на сайт не ходим.

Кеш экономит один HTTP-запрос на статью — это мелочь. Основная цена эталона в том,
что его HTML уходит во входные токены КАЖДОГО запроса article_body, и кеширование
этого не меняет. Если счёт за текст станет заметным, сокращать надо объём эталона
(скелет разметки вместо полного HTML), а не число обращений.
"""

from __future__ import annotations

import io
import re

from PIL import Image
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models.site import Site

# Комментарии вырезаются перед подсчётом: закомментированная картинка не
# рендерится, а лишний сгенерированный кадр стоит денег.
_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
_IMG = re.compile(r"<img\b", re.IGNORECASE)
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_ATTR = re.compile(r'\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)


class ReferenceError(RuntimeError):
    pass


def count_images(html: str) -> int:
    """Сколько <img> в разметке — столько картинок и генерируется для статьи."""
    return len(_IMG.findall(_COMMENTS.sub("", html or "")))


def extract_image_srcs(html: str) -> list[str | None]:
    """По элементу на каждый <img> в порядке появления — так же, как
    count_images, вырезая комментарии тем же паттерном, поэтому длина
    результата всегда совпадает с count_images(html). None на позиции —
    у тега нет src (бывает и в реальной разметке, и в тестовых заглушках
    вида "<img><img>") — measure_reference_image_ratios ниже просто не
    измеряет такую позицию, не роняя всё остальное."""
    stripped = _COMMENTS.sub("", html or "")
    result = []
    for tag in _IMG_TAG.findall(stripped):
        match = _SRC_ATTR.search(tag)
        result.append(match.group(1) if match else None)
    return result


def measure_reference_image_ratios(client, html: str) -> list[str]:
    """Реальные пропорции W:H каждой картинки эталона, в порядке появления —
    именно они потом используются как crop для генерируемой картинки той же
    позиции (ArticleBuilder._crop_for_position, app/articles/builder.py)
    вместо одного захардкоженного CONTENT_CROP="3:2" для всех позиций сразу.
    Найдено на живом сайте (stroybaza-moscow.ru): первая картинка статьи
    рендерится в широком баннере .article-hero (эталон — 1180×488, ≈2.42:1),
    а не 3:2, и генератор до этой правки кадрировал её как обычную
    контентную картинку — получалось заметно выше эталона.

    Картинка, которую не удалось скачать или разобрать как изображение
    (сеть недоступна, битый файл, страница логина вместо файла, src вообще
    отсутствует), даёт пустую строку на своей позиции, а не исключение:
    эталон — чужой сайт, а не наш API, надёжность которого мы не
    контролируем, и один недоступный кадр не должен ронять синхронизацию
    всей карточки — эта позиция просто вернётся к дефолтному CONTENT_CROP."""
    ratios = []
    for src in extract_image_srcs(html):
        if not src:
            ratios.append("")
            continue
        try:
            data = client.fetch_file(src)
            width, height = Image.open(io.BytesIO(data)).size
        except Exception:  # noqa: BLE001 — см. докстринг выше: любая причина
                            # не измерить одну картинку не должна ронять
                            # измерение остальных или всю синхронизацию.
            ratios.append("")
            continue
        ratios.append(f"{width}:{height}")
    return ratios


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
    site.reference_image_ratios = ",".join(measure_reference_image_ratios(client, html))
    site.reference_synced_at = utcnow()
    if commit:
        db.commit()
