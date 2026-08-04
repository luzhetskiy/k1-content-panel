"""
Шаг 1 (новый): Импорт компаний из выгрузки Яндекс.Карт (xlsx).

Заменяет прежний поиск по интернету (step1_search_companies.py). Компании
берутся из готовой выгрузки, оттуда же — достоверные контакты, координаты,
отзвы, логотип и соцсети. Скрейпинг сайта (шаг 2) остаётся только ради
маркетингового текста.

Запуск:
    python execution/step1_import_yandex.py --region "Самара" --sphere "строительство частных домов"
    python execution/step1_import_yandex.py --region "Москва" --sphere "строительство частных домов" --limit 100
    python execution/step1_import_yandex.py --file builders_yandex_2026-07-10.xlsx --region "Россия" --sphere "коттеджные посёлки"
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import openpyxl

sys.path.insert(0, os.path.dirname(__file__))
import db

DEFAULT_FILE = Path(__file__).parent.parent / "builders_yandex_2026-07-10.xlsx"

# ── Маппинг заголовков выгрузки на индексы (заполняется из строки заголовка) ──
# Ожидаемые названия колонок в листе «Яндекс».
COLUMNS = {
    "query": "Запрос",
    "name": "Название",
    "category": "Категории",
    "region": "Регион",
    "city": "Город",
    "address": "Полный адрес",
    "phone_mobile": "Мобильные",
    "phone_landline": "Немобильные",
    "site": "Сайт",
    "email": "Email с сайта компании",
    "schedule": "График",
    "lat": "Широта",
    "lon": "Долгота",
    "ratings": "Оценок",
    "reviews": "Отзывов",
    "rating": "Рейтинг",
    "all_sites": "Все сайты",
    "all_phones": "Все телефоны",
    "logo": "Логотип",
    "yandex_card": "Карточка организации",
    "traffic": "Посещаемость",
}

# Предложный падеж для основных городов (см. directions/regions.md).
# Для остальных city_prepositional остаётся пустым — шаблон подставит имен. падеж.
CITY_PREPOSITIONAL = {
    "Москва": "Москве",
    "Санкт-Петербург": "Санкт-Петербурге",
    "Новосибирск": "Новосибирске",
    "Екатеринбург": "Екатеринбурге",
    "Казань": "Казани",
    "Нижний Новгород": "Нижнем Новгороде",
    "Челябинск": "Челябинске",
    "Самара": "Самаре",
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
    "Новокузнецк": "Новокузнецке",
}

# Ключевые слова для фильтра сферы по колонке «Категории» (нижний регистр,
# подстрочное вхождение). Ключ — значение параметра --sphere из directions/spheres.md.
SPHERE_KEYWORDS = {
    "застройщики": ["застройщик", "многоквартирн", "жилой комплекс", "девелопер"],
    "строительство частных домов": [
        "дачных домов", "коттедж", "частных домов", "загородн", "дом под ключ",
    ],
    "строительство домов": [
        "дачных домов", "коттедж", "частных домов", "загородн", "дом под ключ",
    ],
    "коттеджные посёлки": ["коттеджн", "коттедж"],
    "строительство таунхаусов": ["таунхаус"],
    "деревянное домостроение": ["деревянн", "из бревна", "из бруса", "сруб"],
    "дома из бруса": ["из бруса", "брус"],
    "дома из бревна": ["из бревна", "сруб", "бревн"],
    "каркасные дома": ["каркасн"],
    "дома из клееного бруса": ["клееного бруса", "клееный брус"],
    "бани строительство": ["бан", "саун"],
    "беседки на заказ": ["беседк"],
    "строительство гаражей": ["гараж"],
    "хозяйственные постройки": ["хозяйствен", "хозблок", "сарай"],
    "промышленное строительство": ["промышленн", "гражданское строительство"],
    "строительство складов": ["склад"],
    "строительство торговых центров": ["торгов", "тц"],
    "монолитное строительство": ["монолит"],
    "кирпичные дома": ["кирпичн", "из кирпича"],
    "модульные здания": ["модульн"],
    "быстровозводимые здания": ["быстровозводим", "конструкции"],
    "архитектурное бюро": ["архитектурн", "проектн"],
}

# Значения --region, означающие федеральный охват (без фильтра по региону).
FEDERAL_ALIASES = {"россия", "рф", "вся россия", "все", "all"}


def norm(s) -> str:
    return (str(s).strip().lower()) if s is not None else ""


def build_header_map(header_row: tuple) -> dict:
    """Сопоставляет ключи COLUMNS с индексами столбцов по названиям заголовка."""
    title_to_idx = {}
    for idx, title in enumerate(header_row):
        if title is not None:
            title_to_idx[str(title).strip()] = idx

    mapping = {}
    for key, title in COLUMNS.items():
        if title in title_to_idx:
            mapping[key] = title_to_idx[title]
    missing = [COLUMNS[k] for k in ("name", "region", "city", "category", "site") if k not in mapping]
    if missing:
        raise SystemExit(f"В файле нет обязательных колонок: {missing}. Проверьте формат выгрузки.")
    return mapping


def get(row: tuple, header: dict, key: str):
    idx = header.get(key)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def region_matches(row_city: str, row_region: str, target: str) -> bool:
    t = norm(target)
    if not t or t in FEDERAL_ALIASES:
        return True
    city = norm(row_city)
    region = norm(row_region)
    # Совпадение по городу (напр. «Самара» == «Самара», «Москва» == «Москва»)
    if t == city or (city and city in t):
        return True
    # Вхождение по региону-области (напр. «Самарская область» in region)
    if t in region:
        return True
    return False


def sphere_keywords(sphere: Optional[str]) -> List[str]:
    if not sphere:
        return []
    key = sphere.strip().lower()
    if key in SPHERE_KEYWORDS:
        return SPHERE_KEYWORDS[key]
    # Неизвестная сфера — ищем по самому значению
    return [key]


def sphere_matches(row_category: str, row_query: str, keywords: List[str]) -> bool:
    if not keywords:
        return True
    haystack = f"{norm(row_category)} {norm(row_query)}"
    return any(kw in haystack for kw in keywords)


def normalize_site(url: str) -> Optional[str]:
    if not url:
        return None
    url = str(url).strip().split("|")[0].strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    # Отсекаем UTM/yclid и прочие query-параметры и фрагменты — в выгрузке
    # ссылки часто с рекламными хвостами (?utm_source=..., ?yclid=...).
    parsed = urlparse(url)
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return clean.rstrip("/")


def site_key(url) -> str:
    """Нормализованный ключ сайта для сравнения (без схемы, www, слэша, регистра)."""
    if not url:
        return ""
    s = str(url).lower().strip()
    s = re.sub(r"^https?://", "", s)
    if s.startswith("www."):
        s = s[4:]
    return s.rstrip("/")


def site_text(url: str) -> str:
    try:
        netloc = urlparse(url).netloc
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return url


def first_phone(row: tuple, header: dict) -> Optional[str]:
    """Первый телефон из «Все телефоны», иначе из «Немобильные»/«Мобильные»."""
    for key in ("all_phones", "phone_landline", "phone_mobile"):
        val = get(row, header, key)
        if val:
            return str(val).split("|")[0].strip()
    return None


def phone_to_tel(phone_text: str) -> str:
    """+7 (846) 277-06-05 -> +78462770605"""
    digits = re.sub(r"\D", "", phone_text)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    return "+" + digits if digits else ""


def first_email(row: tuple, header: dict) -> str:
    val = get(row, header, "email")
    if not val:
        return ""
    # Email может быть перечислен через запятую
    return str(val).split(",")[0].strip()


def format_coordinates(row: tuple, header: dict) -> Optional[str]:
    lat = get(row, header, "lat")
    lon = get(row, header, "lon")
    if lat is None or lon is None:
        return None
    try:
        return f"{float(str(lat).replace(',', '.')):.6f}, {float(str(lon).replace(',', '.')):.6f}"
    except (ValueError, TypeError):
        return None


def to_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "."))
    except (ValueError, TypeError):
        return None


def to_int(val) -> int:
    try:
        return int(float(str(val).replace(",", ".")))
    except (ValueError, TypeError):
        return 0


def build_contact(row: tuple, header: dict, site: str) -> dict:
    phone_text = first_phone(row, header) or ""
    address = get(row, header, "address")
    schedule = get(row, header, "schedule")
    return {
        "address": str(address).strip() if address else "",
        "phone_tel": phone_to_tel(phone_text),
        "phone_text": phone_text,
        "email": first_email(row, header),
        "working_hours": str(schedule).strip() if schedule else "",
        "site_url": site,
        "site_text": site_text(site),
        "note": "",
    }


def main():
    parser = argparse.ArgumentParser(description="Импорт компаний из выгрузки Яндекс.Карт")
    parser.add_argument("--region", required=True,
                        help="Регион: город («Самара»), область («Самарская область») или «Россия»")
    parser.add_argument("--sphere", required=True, help="Сфера (см. directions/spheres.md)")
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="Путь к xlsx-выгрузке")
    parser.add_argument("--limit", type=int, default=0,
                        help="Максимум компаний (0 = без ограничения)")
    parser.add_argument("--require-site", action="store_true", default=True,
                        help="Импортировать только компании с сайтом (по умолчанию да)")
    parser.add_argument("--allow-no-site", dest="require_site", action="store_false",
                        help="Импортировать и компании без сайта")
    parser.add_argument("--exclude-existing", action="store_true",
                        help="Пропускать компании, чей сайт уже есть в БД (не берём тех, кого уже делали)")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Файл не найден: {path}")

    db.init_db()

    print(f"\nИмпорт из: {path.name}")
    print(f"Регион: {args.region} | Сфера: {args.sphere} | require_site={args.require_site}")
    print("-" * 60)

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = build_header_map(next(rows))

    keywords = sphere_keywords(args.sphere)

    # Множество уже известных сайтов в БД (для --exclude-existing).
    existing_sites = set()
    if args.exclude_existing:
        with db.get_connection() as conn:
            for r in conn.execute("SELECT website FROM companies"):
                existing_sites.add(site_key(r["website"]))

    seen_sites = set()
    matched = 0
    skipped_no_site = 0
    skipped_existing = 0
    candidates = []  # собираем всё подходящее, затем сортируем по отзывам

    for row in rows:
        row_city = get(row, header, "city")
        row_region = get(row, header, "region")
        row_category = get(row, header, "category")
        row_query = get(row, header, "query")

        if not region_matches(row_city, row_region, args.region):
            continue
        if not sphere_matches(row_category, row_query, keywords):
            continue

        matched += 1

        site = normalize_site(get(row, header, "site"))
        if args.require_site and not site:
            skipped_no_site += 1
            continue
        if not site:
            continue
        key = site_key(site)
        if key in seen_sites:
            continue
        seen_sites.add(key)
        if args.exclude_existing and key in existing_sites:
            skipped_existing += 1
            continue

        name = str(get(row, header, "name") or "").strip()
        if not name:
            continue

        candidates.append({
            "name": name,
            "site": site,
            "reviews": to_int(get(row, header, "reviews")),
            "rating": to_float(get(row, header, "rating")),
            "traffic": to_int(get(row, header, "traffic")),
            "yandex_url": get(row, header, "yandex_card"),
            "city": str(row_city or "").strip(),
            "logo": get(row, header, "logo"),
            "address": str(get(row, header, "address") or "").strip(),
            "contact": build_contact(row, header, site),
            "coordinates": format_coordinates(row, header),
        })

    wb.close()

    # Топ по числу отзывов; при --limit берём лучших N.
    candidates.sort(key=lambda c: -c["reviews"])
    if args.limit:
        candidates = candidates[:args.limit]

    saved = 0
    for c in candidates:
        company_id = db.upsert_company(
            region=args.region,
            sphere=args.sphere,
            name=c["name"],
            website=c["site"],
            traffic_estimate=c["traffic"],
            citations_count=c["reviews"],
            reviews_count=c["reviews"],
            rating=c["rating"],
            yandex_url=str(c["yandex_url"]).strip() if c["yandex_url"] else None,
        )
        logo_src = str(c["logo"]).split("|")[0].strip() if c["logo"] else None
        info = {
            "builder_name": c["name"],
            "city_name": c["city"],
            "city_prepositional": CITY_PREPOSITIONAL.get(c["city"], ""),
            "builder_logo_src": logo_src,
            "builder_logo_alt": c["name"],
            "contacts": [c["contact"]],
            "address": c["address"],
            "coordinates": c["coordinates"],
        }
        db.save_yandex_info(company_id, info)
        saved += 1

    print(f"Совпало по региону+сфере: {matched}")
    if args.require_site:
        print(f"Пропущено без сайта: {skipped_no_site}")
    if args.exclude_existing:
        print(f"Пропущено (уже в БД): {skipped_existing}")
    print(f"Импортировано (уникальных по сайту): {saved}")

    if matched == 0:
        print("\n[!] Ни одна компания не подошла. Проверьте:")
        print("    - есть ли данные по этому региону в файле;")
        print(f"    - покрывает ли выгрузка сферу «{args.sphere}» "
              "(текущий файл — только дачные дома/коттеджи и архитектурные бюро).")
        return

    print("\nТоп по числу отзывов:")
    for c in db.get_companies(region=args.region, sphere=args.sphere, limit=15):
        print(f"  {c['reviews_count']:4d} отз. | рейтинг {c.get('rating') or '-'} "
              f"| {c['name']} ({c['website']})")


if __name__ == "__main__":
    main()
