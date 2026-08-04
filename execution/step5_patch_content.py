"""
Обновление HTML-контента (поле "text") уже опубликованных страниц через PATCH.
Используется после перегенерации шаблона.

Запуск:
    python execution/step5_patch_content.py --site "https://vetonit-center.ru" --sphere "строительство частных домов"
    python execution/step5_patch_content.py --site "https://vetonit-center.ru" --sphere "..." --dry-run
"""

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
import db
from step5_publish_batch import get_site_env, strip_html_comments

load_dotenv()

API_PATH = "/api/v1/staticpages/"


def get_all_pages(base_url: str, token: str) -> dict[str, int]:
    headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
    next_url = base_url.rstrip("/") + API_PATH
    result = {}
    while next_url:
        resp = requests.get(next_url, headers=headers, params={"limit": 100}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("results", []):
            result[item["url"]] = item["id"]
        next_url = data.get("next")
    return result


def patch_page(base_url: str, token: str, page_id: int, fields: dict) -> tuple[bool, int]:
    url = base_url.rstrip("/") + API_PATH + f"{page_id}/"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {token}",
    }
    resp = requests.patch(url, json=fields, headers=headers, timeout=30)
    return resp.ok, resp.status_code


def main():
    parser = argparse.ArgumentParser(description="PATCH text страниц после перегенерации")
    parser.add_argument("--site", required=True, help="Базовый URL сайта")
    parser.add_argument("--sphere", default=None, help="Фильтр по сфере")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    parsed = urlparse(args.site)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    domain = parsed.netloc

    token = get_site_env(domain, "SITE_API_TOKEN")
    if not token:
        print(f"ERROR: нет SITE_API_TOKEN_{domain.removesuffix('.ru')} в .env")
        sys.exit(1)

    db.init_db()

    print(f"Загружаем список страниц с {base_url}...")
    url_to_id = get_all_pages(base_url, token)
    print(f"Всего страниц на сайте: {len(url_to_id)}")

    companies = db.get_companies(sphere=args.sphere, limit=10000)
    targets = []
    for c in companies:
        contents = db.get_generated_content(c["id"], base_url)
        if not contents:
            continue
        page_url_full = contents[0].get("page_url", "")
        if not page_url_full:
            continue
        page_path = urlparse(page_url_full).path
        if page_path not in url_to_id:
            continue
        html = contents[0].get("html_content", "")
        if not html:
            continue
        targets.append((c, page_path, url_to_id[page_path], html))

    print(f"Страниц к обновлению: {len(targets)}")
    print("-" * 70)

    ok = 0
    for company, page_path, page_id, html in targets:
        clean = strip_html_comments(html)

        if args.dry_run:
            print(f"  [{company['id']}] {page_path} — {len(clean)} симв.")
            ok += 1
            continue

        success, status = patch_page(base_url, token, page_id, {"text": clean})
        name = company["name"][:40]
        if success:
            print(f"  [{company['id']}] {name} — OK (HTTP {status})")
            ok += 1
        else:
            print(f"  [{company['id']}] {name} — FAIL (HTTP {status})")

        time.sleep(0.2)

    label = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{label}Итого обновлено: {ok}/{len(targets)}")


if __name__ == "__main__":
    main()
