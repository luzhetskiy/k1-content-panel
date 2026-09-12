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


# Загруженные пользователем медиа-файлы (партнёрские логотипы в контенте
# страницы) — не логотип самой компании, даже если в имени есть "logo".
_SKIP_SRC = re.compile(r"/wp-content/uploads/|/upload/|/media/uploads/")


def _is_logo_candidate(tag) -> bool:
    attrs = " ".join([
        tag.get("id") or "",
        " ".join(tag.get("class", [])),
        tag.get("alt") or "",
        tag.get("aria-label") or "",
    ]).lower()
    return bool(re.search(r"logo|лого", attrs))


def _find_img_in_scope(scope, base_url: str) -> str:
    containers = [t for t in scope.find_all(True) if _is_logo_candidate(t)]
    search_in = containers if containers else [scope]
    for container in search_in:
        for img in container.find_all("img"):
            src = img.get("src", "")
            if not src or _SKIP_SRC.search(src):
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
