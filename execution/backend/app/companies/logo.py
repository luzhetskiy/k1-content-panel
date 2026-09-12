"""Поиск логотипа на сайте компании-строителя — запасной источник, когда в
выгрузке Яндекс.Карт колонка «Логотип» пуста (это ~22% строк). Порт
find_logo/find_logo_in_scope из execution/step2_find_svg_logos.py, дополненный
ленивой загрузкой и inline-<svg>: все картинки строителей по требованию
загружаются в service-img как файлы (см. CompanyBuilder._upload_logo), а не
как встроенная в страницу SVG-разметка."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}
_TIMEOUT_SECONDS = 12


@dataclass(frozen=True)
class LogoCandidate:
    """Логотип бывает двух видов: ссылка на файл (её качаем и перезаливаем)
    и inline-<svg> прямо в разметке шапки (его заливаем как .svg)."""

    url: str = ""
    svg_markup: str = ""

    def __bool__(self) -> bool:
        return bool(self.url or self.svg_markup)


# Логотипы партнёров/клиентов в контенте страницы — не логотип самой
# компании, даже если в имени файла есть "logo". Раньше это отсекалось по
# каталогу (/wp-content/uploads/), но там же лежит и настоящий логотип
# WordPress-сайта, поэтому фильтруем по контейнеру.
_SKIP_CONTAINER = re.compile(r"partner|client|brand|portfolio|gallery|slider", re.I)


def _in_skipped_container(tag) -> bool:
    for parent in tag.parents:
        blob = " ".join([parent.get("id") or "", " ".join(parent.get("class", []))])
        if _SKIP_CONTAINER.search(blob):
            return True
    return False


def _is_logo_candidate(tag) -> bool:
    attrs = " ".join([
        tag.get("id") or "",
        " ".join(tag.get("class", [])),
        tag.get("alt") or "",
        tag.get("aria-label") or "",
    ]).lower()
    return bool(re.search(r"logo|лого", attrs))


# Порядок важен: при ленивой загрузке настоящий адрес лежит в data-атрибуте,
# а src — лишь заглушка (крошечный gif, «lazy.svg», прозрачный data:-URI),
# поэтому data-атрибуты проверяются раньше src, который остаётся резервом.
_SRC_ATTRS = ("data-src", "data-lazy", "data-lazy-src", "data-original", "src")
_SRCSET_ATTRS = ("srcset", "data-srcset")


def _img_src(img) -> str:
    """Адрес картинки с учётом ленивой загрузки. data:-URI не адрес, а
    встроенная заглушка-прозрачность — для логотипа бесполезна."""
    for attr in _SRC_ATTRS:
        value = (img.get(attr) or "").strip()
        if value and not value.startswith("data:"):
            return value
    for attr in _SRCSET_ATTRS:
        value = (img.get(attr) or "").strip()
        if not value:
            continue
        first = value.split(",")[0].strip().split(" ")[0]
        if first and not first.startswith("data:"):
            return first
    return ""


def _find_img_in_scope(scope, base_url: str) -> str:
    containers = [t for t in scope.find_all(True) if _is_logo_candidate(t)]
    search_in = containers if containers else [scope]
    for container in search_in:
        # <img> не может содержать другой <img>: если сам он попал сюда как
        # кандидат-контейнер (например, класс "logo" стоит прямо на нём, без
        # обёртки), find_all("img") ничего не найдёт — нужно проверить его же.
        imgs = [container] if container.name == "img" else container.find_all("img")
        for img in imgs:
            src = _img_src(img)
            if not src or _in_skipped_container(img):
                continue
            if re.search(r"logo|лого", src, re.IGNORECASE) or _is_logo_candidate(img):
                return urljoin(base_url, src)
    return ""


def find_logo(html: str, base_url: str) -> LogoCandidate:
    soup = BeautifulSoup(html, "html.parser")
    scope = (
        soup.find("header")
        or soup.find(id=re.compile(r"header", re.I))
        or soup.find(class_=re.compile(r"header", re.I))
        or soup.find("nav")
    )
    logo_src = _find_img_in_scope(scope or soup, base_url)
    if logo_src:
        return LogoCandidate(url=logo_src)

    for container in [t for t in soup.find_all(True) if _is_logo_candidate(t)]:
        logo_src = _find_img_in_scope(container, base_url)
        if logo_src:
            return LogoCandidate(url=logo_src)
    return LogoCandidate()


def fetch_company_logo(website: str) -> LogoCandidate:
    try:
        response = requests.get(website, headers=_HEADERS, timeout=_TIMEOUT_SECONDS,
                                allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException:
        return LogoCandidate()
    return find_logo(response.text, website.rstrip("/"))
