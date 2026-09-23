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
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import requests

STATICPAGES_PATH = "/api/v1/staticpages/"
ARTICLES_PATH = "/api/v1/articles/"
FILEMANAGER_PATH = "/api/v1/filemanager/"
ADDRESSES_SERVICES_PATH = "/api/v1/addresses-services/"
CATALOG_CATEGORIES_PATH = "/api/v1/catalog-categories/"
METATAGS_PATH = "/api/v1/metatags/"
SITEMAP_PATH = "/sitemap.xml"

# Хостинг сайтов отдаёт cookie-заглушку запросам без браузерного User-Agent
# (см. обход в app/companies/logo.py) — sitemap публичный, токен ему не нужен.
SITEMAP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; k1-content-panel)"}

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
                upload_timeout: int = 120, heavy_timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        # Тяжёлые списки: страница catalog-categories — ~155 КБ с seo_text внутри,
        # 2026-09-16 первое чтение вернуло обрезанное тело.
        self.heavy_timeout = heavy_timeout

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Token {self.token}", "Accept": "application/json"}

    def _check(self, response, what: str):
        if not response.ok:
            raise SiteAPIError(f"{what}: HTTP {response.status_code}: {response.text[:300]}",
                               status_code=response.status_code)
        return response

    def _send(self, fn, url: str, what: str, **kwargs):
        """Единственная точка, через которую клиент ходит в сеть.

        `fn` — `requests.get`/`.post`/`.patch`, передаётся аргументом, а не
        вызывается здесь по имени метода через `requests.request`: существующие
        тесты подменяют `app.sites.client.requests.get`/`.post`/`.patch` через
        monkeypatch, и переход на `requests.request` сломал бы их все сразу,
        потребовав переписывания тестов, не имеющих отношения к сетевым сбоям.
        Передача `fn` от места вызова сохраняет эту поверхность подмены:
        атрибут `requests.get` разрешается в момент исполнения строки вызова,
        то есть уже после monkeypatch.

        Сетевой сбой ниже уровня HTTP (DNS не разрешился, соединение отвергнуто,
        таймаут) приходит как `requests.RequestException` и превращается в
        `SiteAPIError` со `status_code=None` — ровно тот контракт, который
        докстринги `SiteAPIError` и этого модуля обещают с самого начала, но
        который до сих пор не исполнял никто.

        Зачем это понадобилось: 2026-09-03 необёрнутый `ConnectionError` (отказ
        DNS по домену сайта на хосте, см. directions/2026-09-12-batch-
        resilience-design.md §1-2) убил партию 25. Он не входит ни в один
        except по пути — ни в список `ArticleBuilder.build()`, ни в список
        `run_batch_sync` — поэтому задача Celery упала необработанной, и в БД
        не исправилось НИЧЕГО: партия навсегда осталась в "running", 23 статьи
        в "draft". Ошибка такого типа обязана быть отказом ОДНОЙ статьи, а не
        смертью партии; `SiteAPIError` билдер уже умеет обрабатывать именно так.

        Тип исключения попадает в текст: `str()` у `ConnectionError` от отказа
        DNS сам по себе малопонятен, а этот текст идёт прямо в
        `Article.error_text` и показывается менеджеру в таблице партии.

        Ретраев здесь нет сознательно — см. докстринг модуля («сам клиент
        ретраи не делает — это ответственность вызывающего кода»). Для
        мутирующих вызовов (`create_page`, `create_teaser`, `upload_file`)
        наивный повтор к тому же опасен: `ReadTimeout` после отправки тела
        неотличим от «сайт принял и обработал, а ответ не доехал», и повтор
        создаёт вторую страницу или второй тизер — тот самый дубль, от которого
        существует `_guard_duplicate_url` (см. тест
        test_mutating_call_is_not_retried_on_network_failure).
        """
        try:
            response = fn(url, **kwargs)
        except requests.RequestException as exc:
            raise SiteAPIError(f"{what}: сеть недоступна: "
                               f"{type(exc).__name__}: {exc}") from exc
        # _check поднимает SiteAPIError, а он не подкласс RequestException —
        # поэтому HTTP-ошибки except выше не перехватывает и текст про
        # недоступную сеть к ним не приклеивается (см. тест
        # test_http_error_is_not_labelled_as_network_failure).
        return self._check(response, what)

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
            response = self._send(
                requests.get, f"{self.base_url}{STATICPAGES_PATH}?page={page_number}",
                "список страниц", headers=self._headers, timeout=self.timeout)
            body = self._json(response, "список страниц")
            pages += [item for item in body.get("results", [])
                      if (item.get("url") or "").startswith(url_prefix)]
            if not body.get("next"):
                return pages
            page_number += 1

    def get_page(self, page_id: int) -> dict:
        response = self._send(
            requests.get, f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
            f"страница {page_id}", headers=self._headers, timeout=self.timeout)
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
        response = self._send(
            requests.post, f"{self.base_url}{STATICPAGES_PATH}", "создание страницы",
            json=payload,
            headers={**self._headers, "Content-Type": "application/json"},
            timeout=self.timeout)
        return self._json(response, "создание страницы")

    def update_page_text(self, page_id: int, html: str, *, title: str | None = None,
                         meta_description: str | None = None,
                         meta_keywords: str | None = None) -> dict:
        """PATCH тела уже существующей страницы. Используется вне обычного
        потока сборки (build_for создаёт страницу один раз и больше не
        трогает) — для ручного исправления уже опубликованного контента
        (замена путей картинок после коллизии имён в filemanager) и для
        перегенерации текста статьи (ArticleBuilder.regenerate_text,
        directions/2026-09-09-article-full-regeneration-design.md).

        title/meta_description/meta_keywords — именованные и необязательные:
        существующие вызывающие (fix_article_image_collision.py,
        app/companies/builder.py, ArticleBuilder.regenerate_content_images)
        зовут метод с ровно двумя позиционными аргументами и продолжают
        менять только текст. regenerate_text — единственный вызывающий,
        которому нужны все четыре поля в одном PATCH: заголовок, meta и
        текст обновляются на сайте атомарно, а не рассинхронизированной
        парой запросов."""
        payload = {"text": strip_html_comments(html).strip()}
        if title is not None:
            payload["title"] = title
        if meta_description is not None:
            payload["meta_description"] = meta_description
        if meta_keywords is not None:
            payload["meta_keywords"] = meta_keywords
        response = self._send(
            requests.patch, f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
            f"обновление страницы {page_id}", json=payload,
            headers={**self._headers, "Content-Type": "application/json"},
            timeout=self.timeout)
        return self._json(response, f"обновление страницы {page_id}")

    def set_page_cover(self, page_id: int, image_bytes: bytes, filename: str) -> str:
        """teaser_image — ImageField страницы: путём-строкой не задаётся (400),
        только multipart прямо в поле."""
        ctype = mimetypes.guess_type(filename)[0] or "image/webp"
        response = self._send(
            requests.patch, f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
            "загрузка обложки", headers=self._headers,
            files={"teaser_image": (filename, io.BytesIO(image_bytes), ctype)},
            timeout=self.upload_timeout)
        return self._json(response, "загрузка обложки").get("teaser_image", "")

    # --- статьи раздела /api/v1/articles/ ---
    #
    # Отдельный от staticpages ресурс со своей схемой: поля url у него нет
    # вообще (адрес движок собирает сам как /articles/<slug>/), обложка лежит
    # в поле image (а не teaser_image), а label и teaser обязательны.
    # Подробности схемы и живая проверка — в
    # directions/2026-09-23-articles-target-design.md.

    def list_articles(self) -> list[dict]:
        """Все статьи раздела. Фильтра по разделу нет и не нужно: раздел у
        этого ресурса один, в отличие от staticpages, где статьи приходится
        отбирать по префиксу url (см. list_section_pages выше) — поэтому
        здесь хватает общей пагинации _list_all, а не своего цикла."""
        return self._list_all(ARTICLES_PATH, "список статей")

    def get_article(self, article_id: int) -> dict:
        response = self._send(
            requests.get, f"{self.base_url}{ARTICLES_PATH}{article_id}/",
            f"статья {article_id}", headers=self._headers, timeout=self.timeout)
        return self._json(response, f"статья {article_id}")

    def create_article(self, title: str, slug: str, html: str, teaser: str, label: str,
                       meta_description: str = "", meta_keywords: str = "") -> dict:
        payload = {
            "title": title,
            "slug": slug,
            "body": strip_html_comments(html).strip(),
            "teaser": teaser,
            "label": label,
            "published": False,       # черновик: публикует менеджер вручную
            "wide_view": True,
            "meta_description": meta_description,
            "meta_keywords": meta_keywords,
        }
        response = self._send(
            requests.post, f"{self.base_url}{ARTICLES_PATH}", "создание статьи",
            json=payload,
            headers={**self._headers, "Content-Type": "application/json"},
            timeout=self.timeout)
        return self._json(response, "создание статьи")

    def update_article_text(self, article_id: int, html: str, *, title: str | None = None,
                            teaser: str | None = None,
                            meta_description: str | None = None,
                            meta_keywords: str | None = None) -> dict:
        """PATCH тела уже существующей статьи — аналог update_page_text для
        этого ресурса, с той же семантикой необязательных полей (None =
        «не менять»). Тизер здесь не трогается: он собран из
        meta_description при создании, а перегенерация текста меняет и его
        источник — значит, при переданном meta_description тизер тоже
        пересобирается вызывающим (ArticlesTarget.update_text)."""
        payload = {"body": strip_html_comments(html).strip()}
        if title is not None:
            payload["title"] = title
        if teaser is not None:
            payload["teaser"] = teaser
        if meta_description is not None:
            payload["meta_description"] = meta_description
        if meta_keywords is not None:
            payload["meta_keywords"] = meta_keywords
        response = self._send(
            requests.patch, f"{self.base_url}{ARTICLES_PATH}{article_id}/",
            f"обновление статьи {article_id}", json=payload,
            headers={**self._headers, "Content-Type": "application/json"},
            timeout=self.timeout)
        return self._json(response, f"обновление статьи {article_id}")

    def set_article_cover(self, article_id: int, image_bytes: bytes, filename: str) -> str:
        """Обложка статьи — поле image, ImageField: как и teaser_image у
        страницы, строкой-путём не задаётся, только multipart прямо в поле."""
        ctype = mimetypes.guess_type(filename)[0] or "image/webp"
        response = self._send(
            requests.patch, f"{self.base_url}{ARTICLES_PATH}{article_id}/",
            "загрузка обложки статьи", headers=self._headers,
            files={"image": (filename, io.BytesIO(image_bytes), ctype)},
            timeout=self.upload_timeout)
        return self._json(response, "загрузка обложки статьи").get("image", "")

    def create_teaser(self, name: str, slug: str, address: str, phone: str, email: str,
                      website: str, page_url: str, *, category: int, city: int,
                      location: int, coordinates: str = "", description: str = "") -> int:
        """Карточка-тизер услуги — /api/v1/addresses-services/, не обложка
        страницы. is_active=False: включает менеджер вручную, симметрично
        published=False у create_page. coordinates — "lat, lon" из CompanyInfo.
        coordinates (см. app/api/company_batches.py); API принимает список из
        одной такой строки, портируем контракт execution/step6_manage_
        teasers.py — при пустой строке ключ вообще не шлём. description —
        режим работы (contacts[0]["working_hours"]): без него тизеры,
        созданные сервисом, отличались от заведённых руками пустым полем."""
        payload = {
            "name": name, "slug": slug, "address": address, "phone": phone,
            "email": email, "website": website, "page_url": page_url,
            "is_active": False, "location": location, "category": category, "city": city,
            "description": description,
        }
        if coordinates:
            payload["coordinates"] = [coordinates]
        response = self._send(
            requests.post, f"{self.base_url}{ADDRESSES_SERVICES_PATH}", "создание тизера",
            json=payload,
            headers={**self._headers, "Content-Type": "application/json"},
            timeout=self.timeout)
        body = self._json(response, "создание тизера")
        teaser_id = body.get("id")
        if teaser_id is None:
            raise SiteAPIError(f"создание тизера: ответ без id: {body}")
        return teaser_id

    def update_teaser(self, teaser_id: int, name: str, slug: str, address: str, phone: str,
                      email: str, website: str, page_url: str, *, category: int, city: int,
                      location: int, coordinates: str = "", description: str = "") -> int:
        """Пересборка компании (CompanyBuilder._create_teaser) — тот же payload,
        что и create_teaser (см. его докстрок про coordinates и description),
        но PATCH на уже существующий тизер вместо создания дубликата: график
        работы должен обновляться и при пересборке, а не только при первом
        создании."""
        payload = {
            "name": name, "slug": slug, "address": address, "phone": phone,
            "email": email, "website": website, "page_url": page_url,
            "is_active": False, "location": location, "category": category, "city": city,
            "description": description,
        }
        if coordinates:
            payload["coordinates"] = [coordinates]
        response = self._send(
            requests.patch, f"{self.base_url}{ADDRESSES_SERVICES_PATH}{teaser_id}/",
            f"обновление тизера {teaser_id}", json=payload,
            headers={**self._headers, "Content-Type": "application/json"},
            timeout=self.timeout)
        body = self._json(response, f"обновление тизера {teaser_id}")
        return body.get("id", teaser_id)

    # --- каталог и метатеги (directions/2026-09-16-category-meta-design.md) ---

    def _list_all(self, path: str, what: str) -> list[dict]:
        """Все страницы списка DRF: пагинация ?page=N до пустого next."""
        items, page_number = [], 1
        while True:
            response = self._send(
                requests.get, f"{self.base_url}{path}?page={page_number}", what,
                headers=self._headers, timeout=self.heavy_timeout)
            body = self._json(response, what)
            items += body.get("results", [])
            if not body.get("next"):
                return items
            page_number += 1

    def list_catalog_categories(self) -> list[dict]:
        return self._list_all(CATALOG_CATEGORIES_PATH, "список категорий каталога")

    def list_metatags(self) -> list[dict]:
        """Полный список: фильтр ?url= сайт игнорирует (проверено 2026-09-16)."""
        return self._list_all(METATAGS_PATH, "список метатегов")

    def create_metatag(self, url: str, fields: dict) -> dict:
        response = self._send(
            requests.post, f"{self.base_url}{METATAGS_PATH}", "создание метатега",
            json={"url": url, **fields},
            headers={**self._headers, "Content-Type": "application/json"},
            timeout=self.timeout)
        body = self._json(response, "создание метатега")
        if body.get("id") is None:
            raise SiteAPIError(f"создание метатега: ответ без id: {body}")
        return body

    def update_metatag(self, metatag_id: int, fields: dict) -> dict:
        what = f"обновление метатега {metatag_id}"
        response = self._send(
            requests.patch, f"{self.base_url}{METATAGS_PATH}{metatag_id}/", what,
            json=fields, headers={**self._headers, "Content-Type": "application/json"},
            timeout=self.timeout)
        return self._json(response, what)

    def fetch_sitemap_paths(self) -> set[str]:
        """Пути страниц из sitemap.xml; индекс sitemap разворачивается. Url с
        query (~860 фильтр-страниц на stroybaza-moscow.ru) отбрасываются."""
        paths: set[str] = set()
        pending, seen = [f"{self.base_url}{SITEMAP_PATH}"], set()
        while pending:
            url = pending.pop()
            if url in seen:
                continue
            seen.add(url)
            response = self._send(requests.get, url, "sitemap", headers=SITEMAP_HEADERS,
                                  timeout=self.heavy_timeout)
            try:
                root = ElementTree.fromstring(response.content)
            except ElementTree.ParseError as exc:
                raise SiteAPIError(f"sitemap: ответ не XML: {exc}") from exc
            locs = [node.text.strip() for node in root.iter()
                    if node.tag.endswith("loc") and node.text]
            if root.tag.endswith("sitemapindex"):
                pending += locs
                continue
            for loc in locs:
                parts = urlsplit(loc)
                if not parts.query:
                    paths.add(parts.path)
        return paths

    # --- файлы ---

    def upload_file(self, data: bytes, filename: str, upload_to: str) -> str:
        """Возвращает предсказуемый путь /media/{upload_to}{filename}:
        сам ответ пути не содержит, а коллизия имени означает перезапись."""
        upload_to = upload_to.strip("/") + "/"
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self._send(
            requests.post, f"{self.base_url}{FILEMANAGER_PATH}", "загрузка файла",
            headers=self._headers,
            files={"file": (filename, io.BytesIO(data), ctype)},
            data={"upload_to": upload_to},
            timeout=self.upload_timeout)
        return f"/media/{upload_to}{filename}"

    def fetch_file(self, url: str) -> bytes:
        """Скачивает произвольный файл по url — абсолютному или
        относительному base_url (urljoin разворачивает и то, и другое
        одинаково). Используется measure_reference_image_ratios
        (app/sites/reference.py) для измерения реальных пропорций картинок
        эталона. Без Authorization: url приходит из HTML чужой статьи, а не
        от нас, и медиафайлы, на которые ссылается страница, и так публичны
        (иначе их не увидел бы обычный посетитель сайта) — незачем светить
        токен сайта перед хостом, который мог прийти из чужого HTML."""
        absolute = urljoin(self.base_url + "/", url)
        response = self._send(requests.get, absolute, f"файл {url}", timeout=self.timeout)
        return response.content
