"""Скачивание и очистка текста сайта компании — портирует
execution/step2_scrape_company.py (fetch_html/clean_to_text)."""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup

REQUEST_TIMEOUT_SECONDS = 12
TEXT_LIMIT_CHARS = 12_000

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


class ScrapeError(RuntimeError):
    pass


def _clean_to_text(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "header", "footer"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    text = soup.get_text(separator="\n", strip=True)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return f"TITLE: {title}\n\n" + "\n".join(lines)[:TEXT_LIMIT_CHARS]


def fetch_company_text(url: str) -> str:
    try:
        response = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS,
                                allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ScrapeError(f"не удалось загрузить {url}: {exc}") from exc
    return _clean_to_text(response.text)
