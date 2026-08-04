"""
Шаг 6: Управление тизерами компаний через API /api/v1/addresses-services/

Действия:
  create  — пакетное создание тизеров для компаний из БД
  list    — получить список тизеров с сайта
  patch   — частичное обновление тизера по ID

Запуск:
    # Создать тизеры для компаний (category и city — спросить у пользователя)
    python execution/step6_manage_teasers.py create \\
        --site "https://vetonit-center.ru" \\
        --sphere "строительство частных домов" \\
        --regions "Москва,Московская область,Подмосковье" \\
        --category 3 --city 5

    # Список тизеров
    python execution/step6_manage_teasers.py list \\
        --site "https://vetonit-center.ru"

    python execution/step6_manage_teasers.py list \\
        --site "https://vetonit-center.ru" --limit 20 --offset 0

    # Частичное обновление
    python execution/step6_manage_teasers.py patch \\
        --site "https://vetonit-center.ru" \\
        --id 42 \\
        --set is_active=true category=5 city=2
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

load_dotenv()

API_PATH = "/api/v1/addresses-services/"


# ─── helpers ──────────────────────────────────────────────────────────────────

def get_token(domain: str) -> str:
    key = domain.removesuffix(".ru")
    return os.getenv(f"SITE_API_TOKEN_{key}", "")


def make_headers(token: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {token}",
    }


def api_url(base: str) -> str:
    return base.rstrip("/") + API_PATH


def norm_page_url(url) -> str:
    """Нормализованный путь страницы для сравнения тизеров (без домена и слэша)."""
    if not url:
        return ""
    path = urlparse(str(url)).path if "//" in str(url) else str(url)
    return path.strip().rstrip("/").lower()


def fetch_existing_page_urls(base_url: str, token: str) -> set:
    """Собирает page_url всех существующих тизеров сайта (для дедупликации).

    Идём строго по ссылке `next` из ответа (сайт использует постраничную
    пагинацию `?page=N`; ручной offset игнорируется и приводит к зацикливанию).
    """
    urls = set()
    url = api_url(base_url) + "?limit=500"
    pages = 0
    while url and pages < 100:
        pages += 1
        try:
            resp = requests.get(url, headers=make_headers(token), timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[WARN] Не удалось получить существующие тизеры (дедуп пропущен): {e}")
            break
        items = data.get("results", data) if isinstance(data, dict) else data
        for it in items:
            p = norm_page_url(it.get("page_url"))
            if p:
                urls.add(p)
        url = data.get("next") if isinstance(data, dict) else None
    return urls


def parse_set_args(pairs: list[str]) -> dict:
    """Парсит ['key=value', ...] → dict с авто-приведением типов."""
    result = {}
    for pair in pairs:
        if "=" not in pair:
            print(f"[WARN] Игнорируется аргумент без '=': {pair}")
            continue
        k, v = pair.split("=", 1)
        k = k.strip()
        v = v.strip()
        # bool
        if v.lower() in ("true", "yes", "1"):
            result[k] = True
        elif v.lower() in ("false", "no", "0"):
            result[k] = False
        else:
            # int?
            try:
                result[k] = int(v)
            except ValueError:
                result[k] = v
    return result


# ─── action: list ─────────────────────────────────────────────────────────────

def action_list(base_url: str, token: str, limit: int, offset: int) -> None:
    params = {}
    if limit:
        params["limit"] = limit
    if offset:
        params["offset"] = offset

    url = api_url(base_url)
    try:
        resp = requests.get(
            url, params=params,
            headers=make_headers(token),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[ERROR] {e}")
        return

    # Поддержка и пагинированного, и обычного ответа
    items = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(items)) if isinstance(data, dict) else len(items)

    print(f"\nТизеров на сайте: {total} (показано: {len(items)})\n")
    print(f"{'ID':>6}  {'slug':<40}  {'name':<30}  {'active':>6}")
    print("-" * 90)
    for item in items:
        print(
            f"{item.get('id',''):>6}  "
            f"{str(item.get('slug','')):<40}  "
            f"{str(item.get('name','')):<30}  "
            f"{str(item.get('is_active','')):>6}"
        )

    if isinstance(data, dict) and data.get("next"):
        print(f"\n  Следующая страница: {data['next']}")


# ─── action: patch ────────────────────────────────────────────────────────────

def action_patch(base_url: str, token: str, teaser_id: int, fields: dict) -> None:
    if not fields:
        print("[ERROR] Нет полей для обновления (--set key=value ...)")
        return

    url = api_url(base_url) + f"{teaser_id}/"
    print(f"\nPATCH {url}")
    print(f"Поля: {json.dumps(fields, ensure_ascii=False)}")

    try:
        resp = requests.patch(
            url, json=fields,
            headers=make_headers(token),
            timeout=30,
        )
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:500]}

        if resp.ok:
            print(f"OK (HTTP {resp.status_code})")
            print(json.dumps(body, ensure_ascii=False, indent=2)[:600])
        else:
            print(f"FAIL (HTTP {resp.status_code}): {json.dumps(body, ensure_ascii=False)[:400]}")
    except Exception as e:
        print(f"[ERROR] {e}")


# ─── action: create ───────────────────────────────────────────────────────────

def normalize_phone(raw: str) -> str:
    """Приводим к формату API: 11 цифр, начиная с 7 или 8, без +."""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("+"):
        digits = digits[1:]
    # Если начинается с +7 / 7 / 8 и длина 11 — всё ок
    if len(digits) == 11 and digits[0] in ("7", "8"):
        return digits
    # Если 10 цифр — добавляем 7
    if len(digits) == 10:
        return "7" + digits
    return digits


def build_teaser_payload(company: dict, info: dict, page_url_full: str,
                         category: int, city: int, location: int) -> dict:
    """Формируем payload для POST /api/v1/addresses-services/"""
    contacts = info.get("contacts") or []
    if isinstance(contacts, str):
        try:
            contacts = json.loads(contacts)
        except Exception:
            contacts = []

    c = contacts[0] if contacts else {}

    # slug: из page_url убираем /s/ и trailing slash
    page_path = ""
    if page_url_full:
        parsed = urlparse(page_url_full)
        page_path = parsed.path  # например /s/rudom-moskva/
    slug = page_path.removeprefix("/s/").rstrip("/")
    # API ограничивает slug 50 символами — обрезаем по границе дефиса.
    if len(slug) > 50:
        slug = slug[:50].rstrip("-")
        slug = slug.rsplit("-", 1)[0] if "-" in slug else slug

    # Адрес: сначала из контакта, fallback — общий адрес компании
    address = (c.get("address") or "").strip() or (info.get("address") or "").strip()

    # Телефон: API принимает строго 11 цифр без + (формат: 79991234567)
    phone_raw = (c.get("phone_tel") or "").strip()
    phone = normalize_phone(phone_raw) if phone_raw else ""

    # Координаты: из company_info.coordinates
    coords_raw = (info.get("coordinates") or "").strip()
    coordinates = [coords_raw] if coords_raw else []

    payload = {
        "name":        (info.get("builder_name") or company["name"]).strip(),
        "slug":        slug,
        "address":     address,
        "phone":       phone,
        "email":       (c.get("email") or "").strip(),
        "website":     (c.get("site_url") or company.get("website") or "").strip(),
        "description": (c.get("working_hours") or "").strip(),
        "page_url":    page_path,
        "is_active":   False,
        "location":    location,
        "category":    category,
        "city":        city,
    }
    if coordinates:
        payload["coordinates"] = coordinates
    return payload


def create_teaser(company: dict, info: dict, page_url_full: str,
                  base_url: str, token: str,
                  category: int, city: int, location: int) -> bool:
    payload = build_teaser_payload(company, info, page_url_full, category, city, location)
    name = payload["name"]

    if not payload["slug"]:
        print(f"  [{company['id']}] {name} — SKIP: нет slug (страница не создана?)")
        return False

    url = api_url(base_url)
    try:
        resp = requests.post(url, json=payload, headers=make_headers(token), timeout=30)
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:300]}

        if resp.ok:
            teaser_id = body.get("id", "?")
            print(f"  [{company['id']}] {name} — OK (HTTP {resp.status_code}, teaser_id={teaser_id})")
            return True
        else:
            err = json.dumps(body, ensure_ascii=False)[:300]
            print(f"  [{company['id']}] {name} — FAIL (HTTP {resp.status_code}): {err}")
            return False
    except Exception as e:
        print(f"  [{company['id']}] {name} — ERROR: {e}")
        return False


def action_create(base_url: str, token: str, sphere: str,
                  regions: set[str], category: int, city: int,
                  location: int, skip_no_page: bool) -> None:
    db.init_db()

    companies = db.get_companies(sphere=sphere, limit=10000)
    if regions:
        companies = [c for c in companies if c["region"] in regions]

    # Дедупликация: тизеры, уже существующие на сайте (по page_url), не создаём повторно.
    existing_urls = fetch_existing_page_urls(base_url, token)

    print(f"\nЦелевой сайт: {base_url}")
    print(f"Компаний: {len(companies)}")
    print(f"Регионы: {', '.join(sorted(regions)) if regions else 'все'}")
    print(f"category={category}  city={city}  location={location}")
    print(f"Существующих тизеров на сайте: {len(existing_urls)}")
    print("-" * 70)

    ok = 0
    skipped_dup = 0
    for company in companies:
        info = db.get_company_info(company["id"])
        if not info:
            print(f"  [{company['id']}] {company['name']} — SKIP: нет company_info")
            continue

        contents = db.get_generated_content(company["id"], base_url)
        page_url_full = contents[0].get("page_url", "") if contents else ""

        if not page_url_full and skip_no_page:
            print(f"  [{company['id']}] {info.get('builder_name') or company['name']} — SKIP: нет page_url")
            continue

        # Уже есть тизер с таким page_url — пропускаем (идемпотентность).
        if page_url_full and norm_page_url(page_url_full) in existing_urls:
            print(f"  [{company['id']}] {info.get('builder_name') or company['name']} — SKIP: тизер уже существует")
            skipped_dup += 1
            continue

        result = create_teaser(
            company, info, page_url_full,
            base_url, token, category, city, location,
        )
        if result:
            ok += 1
        time.sleep(0.3)

    print(f"\nИтого: {ok}/{len(companies)} тизеров создано (пропущено дублей: {skipped_dup})")


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Управление тизерами через /api/v1/addresses-services/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--site", required=True, help="Базовый URL сайта")
    sub = parser.add_subparsers(dest="action", required=True)

    # ── create ──
    p_create = sub.add_parser("create", help="Пакетное создание тизеров")
    p_create.add_argument("--sphere", required=True, help="Сфера деятельности")
    p_create.add_argument("--regions", help="Регионы через запятую")
    p_create.add_argument("--region", help="Один регион")
    p_create.add_argument("--category", type=int, required=True, help="ID категории")
    p_create.add_argument("--city", type=int, required=True, help="ID города")
    p_create.add_argument("--location", type=int, default=1, help="ID локации (default: 1)")
    p_create.add_argument("--skip-no-page", action="store_true",
                          help="Пропускать компании без созданной страницы")

    # ── list ──
    p_list = sub.add_parser("list", help="Список тизеров")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--offset", type=int, default=0)

    # ── patch ──
    p_patch = sub.add_parser("patch", help="Частичное обновление тизера")
    p_patch.add_argument("--id", type=int, required=True, dest="teaser_id",
                         help="ID тизера")
    p_patch.add_argument("--set", nargs="+", dest="fields", metavar="key=value",
                         help="Поля для обновления: is_active=true category=5 ...")

    args = parser.parse_args()

    parsed = urlparse(args.site)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    domain = parsed.netloc

    token = get_token(domain)
    if not token:
        print(f"ERROR: нет SITE_API_TOKEN_{domain.removesuffix('.ru')} в .env")
        sys.exit(1)

    if args.action == "list":
        action_list(base_url, token, args.limit, args.offset)

    elif args.action == "patch":
        fields = parse_set_args(args.fields or [])
        action_patch(base_url, token, args.teaser_id, fields)

    elif args.action == "create":
        regions = set()
        if args.regions:
            regions = {r.strip() for r in args.regions.split(",")}
        elif args.region:
            regions = {args.region}
        action_create(
            base_url, token,
            sphere=args.sphere,
            regions=regions,
            category=args.category,
            city=args.city,
            location=args.location,
            skip_no_page=args.skip_no_page,
        )


if __name__ == "__main__":
    main()
