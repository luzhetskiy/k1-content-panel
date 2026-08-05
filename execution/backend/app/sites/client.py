"""Клиент API целевого сайта: страницы, загрузка файлов, обложка.

Порт execution/filemanager.py и execution/articles/publish_articles.py —
токен приходит параметром из БД, а не из .env.

Проверено на stroybaza-samara.ru:
- авторизация везде `Authorization: Token ...`; X-STROYKER-KEY из доков даёт 403;
- upload_to — путь относительно каталога «Медиа» (без 'media/' и ведущего слэша),
  несуществующая подпапка создаётся автоматически;
- коллизия имени в filemanager = перезапись без суффикса, поэтому путь строим сами;
- у списка staticpages пагинация `?page=N`, фильтры ?parent= и ?search= игнорируются,
  раздел вычленяется по префиксу url;
- teaser_image — ImageField страницы, строкой не задаётся, только multipart.
"""

from __future__ import annotations

import io
import mimetypes
import re

import requests

STATICPAGES_PATH = "/api/v1/staticpages/"
ARTICLES_PATH = "/api/v1/articles/"
FILEMANAGER_PATH = "/api/v1/filemanager/"

ARTICLE_IMG_DIR = "uploads/article-img/"
SERVICE_IMG_DIR = "uploads/service-img/"

SLUG_LIMIT_PAGES = 70     # существующие url на сайтах обрезаны примерно здесь
SLUG_LIMIT_ARTICLES = 50

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


class SiteAPIError(RuntimeError):
    pass


def slugify(text: str, limit: int = 60) -> str:
    result = "".join(_TRANSLIT.get(c, c) for c in text.lower())
    result = re.sub(r"[^a-z0-9]+", "-", result)
    return result.strip("-")[:limit].strip("-")


def strip_html_comments(html: str) -> str:
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


class SiteClient:
    def __init__(self, base_url: str, token: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Token {self.token}", "Accept": "application/json"}

    def _check(self, response, what: str):
        if not response.ok:
            raise SiteAPIError(f"{what}: HTTP {response.status_code}: {response.text[:300]}")
        return response

    # --- страницы ---

    def list_section_pages(self, url_prefix: str) -> list[dict]:
        """Все страницы раздела. Фильтр ?parent= сайт игнорирует, поэтому
        раздел отбирается по префиксу url на нашей стороне."""
        pages, page_number = [], 1
        while True:
            response = self._check(
                requests.get(f"{self.base_url}{STATICPAGES_PATH}?page={page_number}",
                             headers=self._headers, timeout=self.timeout),
                "список страниц")
            body = response.json()
            pages += [item for item in body.get("results", [])
                      if (item.get("url") or "").startswith(url_prefix)]
            if not body.get("next"):
                return pages
            page_number += 1

    def get_page(self, page_id: int) -> dict:
        return self._check(
            requests.get(f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
                         headers=self._headers, timeout=self.timeout),
            f"страница {page_id}").json()

    def create_page(self, title: str, url: str, html: str, parent_id: int | None,
                    meta_description: str = "", meta_keywords: str = "") -> dict:
        payload = {
            "title": title,
            "url": url,
            "text": strip_html_comments(html).strip(),
            "published": False,       # черновик: публикует менеджер вручную
            "parent": parent_id,
            "wide_view": True,
            "use_editor": False,
            "meta_description": meta_description,
            "meta_keywords": meta_keywords,
        }
        return self._check(
            requests.post(f"{self.base_url}{STATICPAGES_PATH}", json=payload,
                          headers={**self._headers, "Content-Type": "application/json"},
                          timeout=self.timeout),
            "создание страницы").json()

    def set_page_cover(self, page_id: int, image_bytes: bytes, filename: str) -> str:
        """teaser_image — ImageField страницы: путём-строкой не задаётся (400),
        только multipart прямо в поле."""
        ctype = mimetypes.guess_type(filename)[0] or "image/webp"
        response = self._check(
            requests.patch(f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
                           headers=self._headers,
                           files={"teaser_image": (filename, io.BytesIO(image_bytes), ctype)},
                           timeout=120),
            "загрузка обложки")
        return response.json().get("teaser_image", "")

    # --- файлы ---

    def upload_file(self, data: bytes, filename: str, upload_to: str) -> str:
        """Возвращает предсказуемый путь /media/{upload_to}{filename}:
        сам ответ пути не содержит, а коллизия имени означает перезапись."""
        upload_to = upload_to.strip("/") + "/"
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self._check(
            requests.post(f"{self.base_url}{FILEMANAGER_PATH}",
                          headers=self._headers,
                          files={"file": (filename, io.BytesIO(data), ctype)},
                          data={"upload_to": upload_to},
                          timeout=120),
            "загрузка файла")
        return f"/media/{upload_to}{filename}"
