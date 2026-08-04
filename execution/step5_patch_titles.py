"""
Обновление заголовков и мета-тегов уже опубликованных страниц через PATCH.
Используется после исправления склонений регионов в build_title/build_meta_*.

Запуск:
    python execution/step5_patch_titles.py --site "https://vetonit-center.ru"
    python execution/step5_patch_titles.py --site "https://vetonit-center.ru" --dry-run
"""

import argparse
import json
import os
import sys
import time
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
import db
from step5_publish_batch import (
    build_title,
    build_meta_keywords,
    build_meta_description,
    get_site_env,
)

load_dotenv()

API_PATH = "/api/v1/staticpages/"


def get_all_pages(base_url: str, token: str) -> dict[str, int]:
    """Возвращает {url_path: page_id} для всех страниц на сайте."""
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


def patch_page(base_url: str, token: str, page_id: int, fields: dict) -> tuple[bool, int, object]:
    url = base_url.rstrip("/") + API_PATH + f"{page_id}/"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {token}",
    }
    resp = requests.patch(url, json=fields, headers=headers, timeout=30)
    try:
        body = resp.json()
    except Exception:
        body = resp.text[:200]
    return resp.ok, resp.status_code, body


def main():
    parser = argparse.ArgumentParser(description="PATCH-обновление заголовков страниц")
    parser.add_argument("--site", required=True, help="Базовый URL сайта")
    parser.add_argument("--sphere", default=None, help="Фильтр по сфере (опционально)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Показать что будет обновлено, не отправлять запросы")
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
    # Оставляем только с опубликованной страницей на этом сайте
    published = []
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
        info = db.get_company_info(c["id"])
        if not info:
            continue
        published.append((c, info, page_path, url_to_id[page_path]))

    print(f"Страниц к обновлению: {len(published)}")
    if args.dry_run:
        print("\n[DRY RUN] Заголовки после исправления:\n")
    print("-" * 70)

    ok = 0
    for company, info, page_path, page_id in published:
        name = (info.get("builder_name") or company["name"]).strip()
        sphere = company.get("sphere", "")
        region = company.get("region", "")

        new_title = build_title(name, sphere, region)
        new_meta_kw = build_meta_keywords(name, sphere, region)
        new_meta_desc = build_meta_description(name, sphere, region)

        fields = {
            "title": new_title,
            "meta_keywords": new_meta_kw,
            "meta_description": new_meta_desc,
        }

        if args.dry_run:
            print(f"  [{company['id']}] {page_path}")
            print(f"    title: {new_title}")
            continue

        success, status, body = patch_page(base_url, token, page_id, fields)
        if success:
            print(f"  [{company['id']}] {name} — OK (HTTP {status})")
            ok += 1
        else:
            err = json.dumps(body, ensure_ascii=False)[:200] if isinstance(body, dict) else body
            print(f"  [{company['id']}] {name} — FAIL (HTTP {status}): {err}")

        time.sleep(0.2)

    if not args.dry_run:
        print(f"\nИтого обновлено: {ok}/{len(published)}")


if __name__ == "__main__":
    main()
