"""
Публикация статей на целевой сайт как черновиков (staticpages, published=false).

Читает articles_batch_1/articles.json + HTML-файлы из articles_batch_1/html/
и создаёт страницы в разделе parent (например 25 — «Полезные статьи», /blog/).

Запуск:
    python execution/articles/publish_articles.py --dry-run
    python execution/articles/publish_articles.py
    python execution/articles/publish_articles.py --only article_1.html
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

BATCH_DIR = Path(__file__).resolve().parents[2] / "articles_batch_1"
MANIFEST = BATCH_DIR / "articles.json"
API_PATH = "/api/v1/staticpages/"


def site_token(base_url: str) -> str:
    domain = urlparse(base_url).netloc
    return os.getenv(f"SITE_API_TOKEN_{domain.removesuffix('.ru')}", "")


def strip_html_comments(html: str) -> str:
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


def existing_urls(base_url: str, token: str) -> set:
    """Все url уже созданных страниц — чтобы не плодить дубли при повторном запуске."""
    headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
    urls, page = set(), 1
    while True:
        resp = requests.get(f"{base_url}{API_PATH}?page={page}", headers=headers, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        urls.update(i["url"] for i in body["results"] if i.get("url"))
        if not body.get("next"):
            return urls
        page += 1


def publish(article: dict, cfg: dict, token: str, taken: set, dry_run: bool) -> bool:
    base_url = cfg["site"]
    path = f"{cfg['url_prefix']}{article['slug']}/"
    title = article["title"]

    if path in taken:
        print(f"  SKIP  {path} — страница с таким url уже есть")
        return False

    html = (BATCH_DIR / "html" / article["file"]).read_text(encoding="utf-8")
    payload = {
        "title": title,
        "url": path,
        "text": strip_html_comments(html).strip(),
        "published": False,
        "parent": cfg["parent"],
        "wide_view": True,
        "use_editor": False,
        "meta_keywords": article.get("meta_keywords", ""),
        "meta_description": article.get("meta_description", ""),
    }

    if dry_run:
        print(f"  DRY   {path}  ({len(payload['text'])} симв. HTML)  «{title[:60]}…»")
        return True

    resp = requests.post(
        base_url + API_PATH,
        json=payload,
        headers={"Authorization": f"Token {token}", "Content-Type": "application/json"},
        timeout=60,
    )
    if not resp.ok:
        print(f"  FAIL  {path} — HTTP {resp.status_code}: {resp.text[:300]}")
        return False

    created = resp.json()
    print(f"  OK    id={created.get('id')}  {base_url}{created.get('url')}  (черновик)")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", help="Имена html-файлов из манифеста")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = json.loads(MANIFEST.read_text(encoding="utf-8"))
    token = site_token(cfg["site"])
    if not token:
        print(f"ERROR: нет токена для {cfg['site']} в .env")
        sys.exit(1)

    articles = cfg["articles"]
    if args.only:
        articles = [a for a in articles if a["file"] in args.only]

    taken = existing_urls(cfg["site"], token)
    print(f"\nСайт: {cfg['site']}   раздел: {cfg['url_prefix']} (parent={cfg['parent']})")
    print(f"Статей к публикации: {len(articles)}   уже есть страниц на сайте: {len(taken)}")
    print("-" * 70)

    ok = sum(publish(a, cfg, token, taken, args.dry_run) for a in articles)
    print("-" * 70)
    print(f"Итого: {ok}/{len(articles)}")


if __name__ == "__main__":
    main()
