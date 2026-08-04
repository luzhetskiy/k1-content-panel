"""
Шаг 5 (batch): Публикация страниц для набора компаний через API.

Запуск:
    python execution/step5_publish_batch.py --site "https://vetonit-center.ru" --sphere "строительство частных домов"
    python execution/step5_publish_batch.py --site "https://vetonit-center.ru" --sphere "..." --region "Москва"
    python execution/step5_publish_batch.py --site "https://vetonit-center.ru" --regions "Москва,Московская область,Подмосковье"
    python execution/step5_publish_batch.py --site "https://vetonit-center.ru" --regions "Москва,Московская область,Подмосковье" --skip-verify-check
"""

import argparse
import json
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
import db

load_dotenv()

API_PATH = "/api/v1/staticpages/"


# Предложные формы регионов (в + предложный падеж)
_REGION_PREP: dict[str, str] = {
    "Москва": "Москве",
    "Санкт-Петербург": "Санкт-Петербурге",
    "Московская область": "Московской области",
    "Подмосковье": "Подмосковье",
    "Самара": "Самаре",
    "Новосибирск": "Новосибирске",
    "Екатеринбург": "Екатеринбурге",
    "Казань": "Казани",
    "Нижний Новгород": "Нижнем Новгороде",
    "Челябинск": "Челябинске",
    "Омск": "Омске",
    "Ростов-на-Дону": "Ростове-на-Дону",
    "Уфа": "Уфе",
    "Красноярск": "Красноярске",
    "Пермь": "Перми",
    "Воронеж": "Воронеже",
    "Волгоград": "Волгограде",
    "Краснодар": "Краснодаре",
    "Тюмень": "Тюмени",
    "Саратов": "Саратове",
    "Барнаул": "Барнауле",
    "Ижевск": "Ижевске",
    "Ульяновск": "Ульяновске",
    "Хабаровск": "Хабаровске",
    "Иркутск": "Иркутске",
    "Ярославль": "Ярославле",
    "Владивосток": "Владивостоке",
    "Махачкала": "Махачкале",
    "Томск": "Томске",
    "Оренбург": "Оренбурге",
    "Кемерово": "Кемерове",
    "Рязань": "Рязани",
    "Новокузнецк": "Новокузнецке",
    "Россия": "России",
}


def region_prep(region: str) -> str:
    """Возвращает форму региона для конструкции 'в {region_prep}'."""
    return _REGION_PREP.get(region, region)


def get_site_env(site_domain: str, prefix: str) -> str:
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
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


def build_title(name: str, sphere: str, region: str) -> str:
    return f"{name} — {sphere} в {region_prep(region)}"


def build_page_url(name: str, region: str) -> str:
    slug = slugify(f"{name} {region}")
    return f"/s/{slug}/"


def build_meta_keywords(name: str, sphere: str, region: str) -> str:
    prep = region_prep(region)
    parts = [name, sphere, region,
             f"{sphere} {region}", f"{name} официальный сайт", f"{sphere} в {prep}"]
    return ", ".join(p for p in parts if p)


def build_meta_description(name: str, sphere: str, region: str) -> str:
    return (
        f"{name} — {sphere} в {region_prep(region)}. "
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
            return None, resp.status_code, body
        return body, resp.status_code, body
    except Exception as e:
        return None, None, {"error": str(e)}


def publish_company(company: dict, base_url: str, token: str, parent_id: int,
                    skip_verify: bool) -> bool:
    company_id = company["id"]

    contents = db.get_generated_content(company_id, base_url)
    if not contents:
        print(f"  [{company_id}] нет контента для {base_url} — пропускаем")
        return False

    content = contents[0]

    if not skip_verify and not content.get("verified"):
        print(f"  [{company_id}] не верифицирован — пропускаем (--skip-verify-check чтобы игнорировать)")
        return False

    # Уже опубликован?
    if content.get("page_url"):
        print(f"  [{company_id}] уже опубликован: {content['page_url']}")
        return True

    info = db.get_company_info(company_id)
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

    response, status, body = post_to_api(base_url, payload, token)

    if response is None:
        err = json.dumps(body, ensure_ascii=False)[:300]
        print(f"  [{company_id}] {name} — FAIL (HTTP {status}): {err}")
        return False

    page_url = response.get("url") or response.get("absolute_url") or ""
    if page_url and not page_url.startswith("http"):
        page_url = base_url + page_url

    db.update_page_url(company_id, base_url, page_url or base_url + payload["url"])
    print(f"  [{company_id}] {name} — OK (HTTP {status}) → {page_url or payload['url']}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Batch-публикация страниц через API")
    parser.add_argument("--site", required=True, help="Базовый URL сайта")
    parser.add_argument("--sphere", help="Фильтр по сфере")
    parser.add_argument("--region", help="Один регион")
    parser.add_argument("--regions", help="Несколько регионов через запятую")
    parser.add_argument("--parent", type=int, help="ID родительской страницы (приоритет над .env)")
    parser.add_argument("--skip-verify-check", action="store_true")
    args = parser.parse_args()

    db.init_db()

    from urllib.parse import urlparse
    parsed = urlparse(args.site)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    domain = parsed.netloc

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

    # Собираем регионы
    target_regions = set()
    if args.regions:
        target_regions = {r.strip() for r in args.regions.split(",")}
    elif args.region:
        target_regions = {args.region}

    # Получаем все компании
    companies = db.get_companies(sphere=args.sphere, limit=10000)
    if target_regions:
        companies = [c for c in companies if c["region"] in target_regions]

    print(f"\nЦелевой сайт: {base_url}")
    print(f"Parent ID: {parent_id}")
    print(f"Компаний к публикации: {len(companies)}")
    print(f"Регионы: {', '.join(sorted(target_regions)) if target_regions else 'все'}")
    print("-" * 60)

    ok = 0
    for company in companies:
        result = publish_company(company, base_url, token, parent_id, args.skip_verify_check)
        if result:
            ok += 1
        time.sleep(0.3)

    print(f"\nИтого: {ok}/{len(companies)} опубликовано")
    if ok < len(companies):
        print(f"Не опубликовано: {len(companies) - ok}")


if __name__ == "__main__":
    main()
