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

Замечания ревью (сверка с рабочими скриптами и app/ai/images.py):
- `SiteAPIError.status_code` хранит HTTP-код ответа (None для ошибок разбора
  тела и сетевых сбоев) — вызывающий код (Task 11/18) решает по нему, есть ли
  смысл повторить запрос: 5xx и сетевые таймауты — да, 400/401/403/404/413 —
  нет (та же граница, что и для RouterAI в app/ai/text.py). Сам клиент
  ретраи не делает — это ответственность вызывающего кода;
- тело успешного ответа не гарантированно JSON: прокси, страница логина или
  обрыв соединения посреди тела отдают 200 с мусором. `.json()` всегда
  обёрнут — иначе наружу летит голый json.JSONDecodeError вместо
  SiteAPIError (тот же класс дефекта, что уже закрыт в app/ai/images.py для
  ответов RouterAI);
- `timeout` всегда берётся из атрибутов клиента, а не зашит числом в теле
  метода: `self.timeout` — для чтения/создания страниц, `self.upload_timeout`
  — для загрузки файлов и обложки (файлы крупнее, дефолт больше).
"""

from __future__ import annotations

import io
import mimetypes
import re

import requests

STATICPAGES_PATH = "/api/v1/staticpages/"
ARTICLES_PATH = "/api/v1/articles/"
FILEMANAGER_PATH = "/api/v1/filemanager/"
ADDRESSES_SERVICES_PATH = "/api/v1/addresses-services/"

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
    """status_code — HTTP-код ответа сайта; None для ошибок разбора тела
    (сайт вернул 200, но не JSON) и для сетевых сбоев ниже уровня HTTP."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def slugify(text: str, limit: int = 60) -> str:
    result = "".join(_TRANSLIT.get(c, c) for c in text.lower())
    result = re.sub(r"[^a-z0-9]+", "-", result)
    return result.strip("-")[:limit].strip("-")


def normalize_phone(raw: str) -> str:
    """API тизера (создание карточки-тизера) принимает phone строго как 11
    цифр, начинающихся с 7 или 8 — иначе 400 "Правильный формат телефона...".
    Сырой телефон из выгрузки Яндекс.Карт приходит в произвольном написании
    (+7 (846) 277-06-05, дефисы, пробелы) и иногда содержит несколько номеров
    через запятую (см. миграцию 9864d416847d_widen_company_candidate_phone) —
    берём только первый номер. Формат, который не удаётся привести к 10 или
    11 цифрам, не форсим угадыванием: пустая строка (поле необязательное)
    безопаснее, чем гарантированный отказ создания тизера мусором."""
    first = re.split(r"[,;/]", raw or "", maxsplit=1)[0]
    digits = re.sub(r"\D", "", first)
    if len(digits) == 11 and digits[0] in ("7", "8"):
        return digits
    if len(digits) == 10 and digits[0] != "0":
        return "7" + digits
    return ""


def strip_html_comments(html: str) -> str:
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


class SiteClient:
    def __init__(self, base_url: str, token: str, timeout: int = 60,
                upload_timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.upload_timeout = upload_timeout

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Token {self.token}", "Accept": "application/json"}

    def _check(self, response, what: str):
        if not response.ok:
            raise SiteAPIError(f"{what}: HTTP {response.status_code}: {response.text[:300]}",
                               status_code=response.status_code)
        return response

    def _json(self, response, what: str):
        """Тело успешного ответа не гарантированно JSON: прокси, страница
        логина или обрыв соединения посреди тела отдают 200 с мусором. Без
        обёртки сюда долетает голый json.JSONDecodeError вместо SiteAPIError —
        тот же класс дефекта, что уже закрыт в app/ai/images.py."""
        try:
            return response.json()
        except ValueError as exc:
            raise SiteAPIError(f"{what}: сайт вернул не JSON: {response.text[:300]}") from exc

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
            body = self._json(response, "список страниц")
            pages += [item for item in body.get("results", [])
                      if (item.get("url") or "").startswith(url_prefix)]
            if not body.get("next"):
                return pages
            page_number += 1

    def get_page(self, page_id: int) -> dict:
        response = self._check(
            requests.get(f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
                         headers=self._headers, timeout=self.timeout),
            f"страница {page_id}")
        return self._json(response, f"страница {page_id}")

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
        response = self._check(
            requests.post(f"{self.base_url}{STATICPAGES_PATH}", json=payload,
                          headers={**self._headers, "Content-Type": "application/json"},
                          timeout=self.timeout),
            "создание страницы")
        return self._json(response, "создание страницы")

    def update_page_text(self, page_id: int, html: str) -> dict:
        """PATCH тела уже существующей страницы. Используется вне обычного
        потока сборки (build_for создаёт страницу один раз и больше не
        трогает) — для ручного исправления уже опубликованного контента,
        например замены путей картинок после коллизии имён в filemanager."""
        payload = {"text": strip_html_comments(html).strip()}
        response = self._check(
            requests.patch(f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
                           json=payload,
                           headers={**self._headers, "Content-Type": "application/json"},
                           timeout=self.timeout),
            f"обновление страницы {page_id}")
        return self._json(response, f"обновление страницы {page_id}")

    def set_page_cover(self, page_id: int, image_bytes: bytes, filename: str) -> str:
        """teaser_image — ImageField страницы: путём-строкой не задаётся (400),
        только multipart прямо в поле."""
        ctype = mimetypes.guess_type(filename)[0] or "image/webp"
        response = self._check(
            requests.patch(f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
                           headers=self._headers,
                           files={"teaser_image": (filename, io.BytesIO(image_bytes), ctype)},
                           timeout=self.upload_timeout),
            "загрузка обложки")
        return self._json(response, "загрузка обложки").get("teaser_image", "")

    def create_teaser(self, name: str, slug: str, address: str, phone: str, email: str,
                      website: str, page_url: str, *, category: int, city: int,
                      location: int, coordinates: str = "") -> int:
        """Карточка-тизер услуги — /api/v1/addresses-services/, не обложка
        страницы. is_active=False: включает менеджер вручную, симметрично
        published=False у create_page. coordinates — "lat, lon" из CompanyInfo.
        coordinates (см. app/api/company_batches.py); API принимает список из
        одной такой строки, портируем контракт execution/step6_manage_
        teasers.py — при пустой строке ключ вообще не шлём."""
        payload = {
            "name": name, "slug": slug, "address": address, "phone": phone,
            "email": email, "website": website, "page_url": page_url,
            "is_active": False, "location": location, "category": category, "city": city,
        }
        if coordinates:
            payload["coordinates"] = [coordinates]
        response = self._check(
            requests.post(f"{self.base_url}{ADDRESSES_SERVICES_PATH}", json=payload,
                          headers={**self._headers, "Content-Type": "application/json"},
                          timeout=self.timeout),
            "создание тизера")
        body = self._json(response, "создание тизера")
        teaser_id = body.get("id")
        if teaser_id is None:
            raise SiteAPIError(f"создание тизера: ответ без id: {body}")
        return teaser_id

    def update_teaser(self, teaser_id: int, name: str, slug: str, address: str, phone: str,
                      email: str, website: str, page_url: str, *, category: int, city: int,
                      location: int, coordinates: str = "") -> int:
        """Пересборка компании (CompanyBuilder._create_teaser) — тот же payload,
        что и create_teaser (см. его докстрок про coordinates), но PATCH на
        уже существующий тизер вместо создания дубликата."""
        payload = {
            "name": name, "slug": slug, "address": address, "phone": phone,
            "email": email, "website": website, "page_url": page_url,
            "is_active": False, "location": location, "category": category, "city": city,
        }
        if coordinates:
            payload["coordinates"] = [coordinates]
        response = self._check(
            requests.patch(f"{self.base_url}{ADDRESSES_SERVICES_PATH}{teaser_id}/", json=payload,
                          headers={**self._headers, "Content-Type": "application/json"},
                          timeout=self.timeout),
            f"обновление тизера {teaser_id}")
        body = self._json(response, f"обновление тизера {teaser_id}")
        return body.get("id", teaser_id)

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
                          timeout=self.upload_timeout),
            "загрузка файла")
        return f"/media/{upload_to}{filename}"
