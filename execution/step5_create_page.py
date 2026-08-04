"""
Шаг 5: Создание страницы на сайте через API /api/v1/staticpages/
Страницы создаются неопубликованными (черновик).

Запуск:
    python execution/step5_create_page.py --company-id 5 --site "https://stroybaza-samara.ru"
    python execution/step5_create_page.py --company-id 5 --site "https://stroybaza-samara.ru" --skip-verify-check
"""

import argparse
import json
import os
import re
import sys

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
import db

load_dotenv()

API_PATH = "/api/v1/staticpages/"


def get_site_env(site_domain: str, prefix: str) -> str:
    """Читаем переменную окружения вида PREFIX_{домен без .ru}."""
    key_suffix = site_domain.removesuffix(".ru")
    return os.getenv(f"{prefix}_{key_suffix}", "")


def slugify(text: str) -> str:
    translit = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    result = ""
    for char in text.lower():
        result += translit.get(char, char)
    result = re.sub(r"[^a-z0-9]+", "-", result)
    return result.strip("-")[:80]


def strip_html_comments(html: str) -> str:
    """Удаляем HTML-комментарии из контента перед отправкой."""
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


def build_title(name: str, sphere: str, region: str) -> str:
    return f"{name} — {sphere} в {region}"


def build_page_url(name: str, region: str) -> str:
    """URL вида /s/nazvanie-kompanii-region/"""
    slug = slugify(f"{name} {region}")
    return f"/s/{slug}/"


def build_meta_keywords(name: str, sphere: str, region: str) -> str:
    parts = [name, sphere, region]
    # Добавляем вариации
    parts += [
        f"{sphere} {region}",
        f"{name} официальный сайт",
        f"{sphere} в {region}",
    ]
    return ", ".join(p for p in parts if p)


def build_meta_description(name: str, sphere: str, region: str) -> str:
    return (
        f"{name} — {sphere} в {region}. "
        f"Информация о компании, контакты, услуги и проекты."
    )


def post_to_api(base_url: str, payload: dict, token: str):
    url = base_url.rstrip("/") + API_PATH
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {token}",
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:500]}
        if not resp.ok:
            print(f"  [ERROR] HTTP {resp.status_code}: {json.dumps(body, ensure_ascii=False)[:500]}")
            return None, resp.status_code
        return body, resp.status_code
    except Exception as e:
        print(f"  [ERROR] Request failed: {e}")
        return None, None


def main():
    parser = argparse.ArgumentParser(description="Публикация страницы через API")
    parser.add_argument("--company-id", type=int, required=True, help="ID компании")
    parser.add_argument("--site", required=True, help="Базовый URL сайта (напр. https://stroybaza-samara.ru)")
    parser.add_argument("--parent", type=int, help="ID родительской страницы (приоритет над env)")
    parser.add_argument("--skip-verify-check", action="store_true", help="Пропустить проверку верификации")
    args = parser.parse_args()

    db.init_db()

    from urllib.parse import urlparse
    parsed = urlparse(args.site)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    domain = parsed.netloc  # напр. stroybaza-samara.ru

    token = get_site_env(domain, "SITE_API_TOKEN")
    if not token:
        print(f"ERROR: не найден SITE_API_TOKEN_{domain.removesuffix('.ru')} в .env")
        sys.exit(1)

    parent_id = args.parent
    if parent_id is None:
        raw = get_site_env(domain, "SITE_PARENT_ID")
        if raw:
            parent_id = int(raw)
        else:
            print(f"ERROR: не найден SITE_PARENT_ID_{domain.removesuffix('.ru')} в .env и не передан --parent")
            sys.exit(1)

    # Получаем компанию
    all_companies = db.get_companies(limit=10000)
    company = next((c for c in all_companies if c["id"] == args.company_id), None)
    if not company:
        print(f"ERROR: компания id={args.company_id} не найдена")
        sys.exit(1)

    contents = db.get_generated_content(args.company_id, base_url)
    if not contents:
        print(f"ERROR: нет сгенерированного контента для {base_url}. Сначала step3.")
        sys.exit(1)

    content = contents[0]
    if not args.skip_verify_check and not content["verified"]:
        print("ERROR: контент не верифицирован. Сначала step4 или используйте --skip-verify-check")
        sys.exit(1)

    info = db.get_company_info(args.company_id)
    name = (info or {}).get("builder_name") or company["name"]
    sphere = company.get("sphere", "")
    region = company.get("region", "")

    clean_html = strip_html_comments(content["html_content"])

    page_url_path = build_page_url(name, region)
    page_key = page_url_path.removeprefix("/s/").rstrip("/")

    payload = {
        "title": build_title(name, sphere, region),
        "url": page_url_path,
        "key": page_key,
        "text": clean_html,
        "published": False,
        "meta_keywords": build_meta_keywords(name, sphere, region),
        "meta_description": build_meta_description(name, sphere, region),
        "wide_view": True,
        "use_editor": False,
        "parent": parent_id,
    }

    print(f"\nКомпания: {name}")
    print(f"Сайт: {base_url}")
    print(f"Title: {payload['title']}")
    print(f"URL: {payload['url']}")
    print(f"Key: {payload['key']}")
    print(f"Parent: {parent_id}")
    print("-" * 60)

    response, status = post_to_api(base_url, payload, token)
    if not response:
        print("FAIL: страница не создана")
        sys.exit(1)

    page_url = response.get("url") or response.get("absolute_url") or ""
    if page_url and not page_url.startswith("http"):
        page_url = base_url + page_url

    db.update_page_url(args.company_id, base_url, page_url or base_url + payload["url"])
    print(f"OK: страница создана (черновик), HTTP {status}")
    if page_url:
        print(f"   URL: {page_url}")
    print(f"   Ответ: {json.dumps(response, ensure_ascii=False)[:400]}")


if __name__ == "__main__":
    main()
