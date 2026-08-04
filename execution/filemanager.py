"""
Общий стандарт загрузки файлов на целевые сайты через их API.

Единый метод для строителей и статей: POST /api/v1/filemanager/
(multipart, авторизация обычным Token сайта). Метод перезаписывает файл
с тем же именем — путь /media/{upload_to}{имя} предсказуем.

Стандартные каталоги (относительно каталога «Медиа», т.е. внутри /media/):
    ARTICLE_IMG_DIR = uploads/article-img/   — картинки статей
    SERVICE_IMG_DIR = uploads/service-img/   — файлы страниц строителей (логотипы и т.п.)

Папка uploads/ открыта в админке сайта для ручного просмотра/правки.

Заметки по API (проверено на stroybaza-samara.ru, авг. 2026):
- Заголовок X-STROYKER-KEY из документации НЕ работает (403);
  рабочая авторизация — Authorization: Token {SITE_API_TOKEN_...}.
- upload_to — путь ОТНОСИТЕЛЬНО каталога «Медиа»: для /media/uploads/... передаём
  'uploads/...' (без 'media/' и без ведущего слэша). Несуществующая папка создаётся.
- Разрешён только POST: удаления/листинга через API нет.
- teaser_image у staticpages сюда не относится — это ImageField самой страницы,
  задаётся отдельной multipart-загрузкой в эндпоинт страницы (см. upload_page_teaser).
"""

from __future__ import annotations

import io
import mimetypes
import os
import re
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

FILEMANAGER_PATH = "/api/v1/filemanager/"
STATICPAGES_PATH = "/api/v1/staticpages/"

ARTICLE_IMG_DIR = "uploads/article-img/"
SERVICE_IMG_DIR = "uploads/service-img/"

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(text: str, limit: int = 60) -> str:
    result = "".join(_TRANSLIT.get(c, c) for c in text.lower())
    result = re.sub(r"[^a-z0-9]+", "-", result)
    return result.strip("-")[:limit]


def base_url_of(site: str) -> str:
    p = urlparse(site if "://" in site else "https://" + site)
    return f"{p.scheme}://{p.netloc}"


def site_token(site: str) -> str:
    domain = urlparse(base_url_of(site)).netloc
    return os.getenv(f"SITE_API_TOKEN_{domain.removesuffix('.ru')}", "")


def _ext_from(url: str, content_type: str) -> str:
    """Расширение по URL, иначе по content-type. Без точки."""
    path_ext = os.path.splitext(urlparse(url).path)[1].lstrip(".").lower()
    if path_ext in {"png", "jpg", "jpeg", "webp", "gif", "svg"}:
        return "jpg" if path_ext == "jpeg" else path_ext
    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip() or "")
    return (guessed or ".bin").lstrip(".").replace("jpeg", "jpg")


def upload_bytes(site: str, data: bytes, filename: str, upload_to: str,
                 token: str = "", content_type: str = "application/octet-stream") -> str:
    """Загружает байты в {upload_to}{filename}. Возвращает локальный путь /media/...."""
    base = base_url_of(site)
    token = token or site_token(site)
    if not token:
        raise RuntimeError(f"нет SITE_API_TOKEN для {base}")

    upload_to = upload_to.strip("/") + "/"
    headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
    resp = requests.post(
        base + FILEMANAGER_PATH,
        headers=headers,
        files={"file": (filename, io.BytesIO(data), content_type)},
        data={"upload_to": upload_to},
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f"filemanager {resp.status_code}: {resp.text[:300]}")
    return f"/media/{upload_to}{filename}"


def upload_file(site: str, local_path: str, upload_to: str,
                filename: str = "", token: str = "") -> str:
    filename = filename or os.path.basename(local_path)
    with open(local_path, "rb") as f:
        data = f.read()
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return upload_bytes(site, data, filename, upload_to, token, ctype)


def download_and_upload(site: str, source_url: str, upload_to: str,
                        filename_base: str, token: str = "") -> str:
    """Скачивает внешний файл и заливает на сайт. filename_base — имя без расширения.
    Возвращает локальный путь /media/... (или пустую строку при неудаче скачивания)."""
    try:
        r = requests.get(source_url, timeout=30)
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"не удалось скачать {source_url}: {e}")
    ctype = r.headers.get("Content-Type", "")
    ext = _ext_from(source_url, ctype)
    filename = f"{slugify(filename_base)}.{ext}"
    return upload_bytes(site, r.content, filename, upload_to, token,
                        ctype.split(";")[0].strip() or "application/octet-stream")


def upload_page_teaser(site: str, page_id: int, image_bytes: bytes,
                       filename: str, token: str = "") -> str:
    """Задаёт teaser_image у staticpage прямой multipart-загрузкой (не через filemanager:
    это ImageField страницы, путём-строкой не задаётся). Возвращает URL тизера."""
    base = base_url_of(site)
    token = token or site_token(site)
    ctype = mimetypes.guess_type(filename)[0] or "image/webp"
    resp = requests.patch(
        f"{base}{STATICPAGES_PATH}{page_id}/",
        headers={"Authorization": f"Token {token}", "Accept": "application/json"},
        files={"teaser_image": (filename, io.BytesIO(image_bytes), ctype)},
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f"teaser patch {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("teaser_image", "")
