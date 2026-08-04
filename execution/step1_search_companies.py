"""
Шаг 1: Поиск строительных компаний и сохранение в базу.

Запуск:
    python execution/step1_search_companies.py --region "Самара" --sphere "многоквартирные застройщики" --limit 50
"""

import argparse
import os
import re
import sys
import time
from urllib.parse import urlparse

from duckduckgo_search import DDGS
from dotenv import load_dotenv
from typing import List, Optional

sys.path.insert(0, os.path.dirname(__file__))
import db

load_dotenv()

# Домены, которые НЕ являются сайтами компаний
AGGREGATOR_DOMAINS = [
    # Недвижимость — агрегаторы
    "avito.ru", "cian.ru", "domclick.ru", "m2.ru", "realt.ru", "mirkvartir.ru",
    "novostroy.ru", "domofond.ru", "kvartira.com", "etagi.com", "n1.ru",
    "sob.ru", "irr.ru", "gde.ru", "metrprice.ru", "emls.ru",
    "novostroiki.ru", "newflat.ru", "bn.ru", "move.ru", "kvadroom.ru",
    "restate.ru", "vsemetri.com", "gid.house", "vzt.ru", "nn.ru",
    "nlre.ru", "terrafin.ru", "gdeetotdom.ru",
    # Каталоги и рейтинги строительных компаний
    "blizko.ru", "zoon.ru", "flamp.ru", "otzovik.com", "irecommend.ru",
    "profi.ru", "youdo.com", "yell.ru", "spr.ru", "orgpage.ru",
    "ratingfirmporemontu.ru", "vsestroitelstvo.ru", "domostroyrf.ru",
    "stroitelstvo.ru", "allbiz.ru", "biz360.ru", "firmika.ru",
    "vseocompanii.ru", "2business.ru", "rucompany.ru",
    "spbguru.ru", "samara.blizko.ru",
    # Специфичные строительные агрегаторы
    "erzrf.ru", "nash.dom.rf", "наш.дом.рф",
    "xn--80az8a.xn--d1aqf.xn--p1ai",
    "xn--b1agapfwapgcl.xn--p1ai",
    "xn--80aafbc7bz2ahlb6l.xn--p1ai",
    "xn----dtbfdhlba9adjjd2bcn.xn--p1ai",
    "vseostroyke.rf", "vsestroitelstvo.rf",
    # Поиск и карты
    "yandex.ru", "google.com", "2gis.ru", "bing.com", "mail.ru", "duckduckgo.com",
    # Соцсети
    "vk.com", "instagram.com", "facebook.com", "ok.ru", "youtube.com",
    "telegram.org", "t.me", "twitter.com", "tiktok.com",
    # СМИ и новости
    "kp.ru", "mk.ru", "rg.ru", "aif.ru", "ria.ru", "tass.ru",
    "interfax.ru", "kommersant.ru", "vedomosti.ru", "forbes.ru",
    "realnoevremya.ru", "oboz.info", "63.ru", "sgpress.ru",
    "samara.ru", "samaraonline.ru", "samarskayagazeta.ru",
    "volga-news.ru", "samaratoday.ru", "volga.news",
    "lenta.ru", "gazeta.ru", "iz.ru", "klerk.ru",
    # Аналитика и бизнес-справочники
    "spark-interfax.ru", "kontur.ru", "oborudunion.ru",
    "rusprofile.ru", "focus.kontur.ru", "sbis.ru", "saby.ru",
    "list-org.com", "egrul.nalog.ru", "zachestnyibiznes.ru",
    "checko.ru", "kartoteka.ru", "k2.agency",
    "companies.rbc.ru", "rbc.ru",
    # ЖКХ и управляющие компании
    "my-gkh.ru", "dom.gosuslugi.ru",
    # Государственные
    "gosuslugi.ru", "nalog.ru", "rnp.gov.ru",
    "samadm.ru", "gordumasamara.ru", "promadm.ru",
    "samara-portal.ru", "adm.samara.ru",
    # Энциклопедии
    "wikipedia.org", "ru.wikipedia.org", "wikidata.org",
    # Дизайн-порталы
    "homify.ru", "houzz.com",
    # Прочее нерелевантное
    "marinetraffic.com", "build.ru", "vsedlyastroiki.ru",
]

DOMAIN_EXCLUDES = ["spb", "piter", "krasnodar", "krym", "crimea", "ural", "ekb", "nsk", "novosibirsk"]

AGGREGATOR_PATTERNS = [
    r"novostroy", r"nedvizhim", r"realty", r"estate",
    r"vbr\.ru", r"ninja\.", r"tbank\.ru",
    r"ssau\.ru", r"journals\.", r"klerk\.",
]


def extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.scheme + "://" + parsed.netloc
    except Exception:
        return url


def get_base_domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        parts = netloc.lstrip("www.").split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return netloc
    except Exception:
        return url


def is_valid_site(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower().lstrip("www.")
        base = get_base_domain(url)
        for agg in AGGREGATOR_DOMAINS:
            if agg in netloc or netloc == agg or base == agg:
                return False
        if re.search(r"\.(gov|msk|spb|samara)\.ru$", netloc):
            return False
        for excl in DOMAIN_EXCLUDES:
            if excl in netloc:
                return False
        for pat in AGGREGATOR_PATTERNS:
            if re.search(pat, netloc):
                return False
        return True
    except Exception:
        return False


def ddg_search(query: str, max_results: int = 10) -> List[dict]:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return results
    except Exception as e:
        print(f"  [WARN] DDG error: {e}")
        return []


def count_citations(company_name: str) -> int:
    results = ddg_search(f'"{company_name}"', max_results=10)
    return len(results)


def extract_company_names_from_text(text: str) -> List[str]:
    names = []
    patterns = [
        r'(?:ГК|СК|МФК|ООО|ОАО|ЗАО|АО|ПАО)\s+[«"]?([А-ЯЁа-яё][а-яёА-ЯЁ\s\-]{2,25})[»"]?(?=[\s,\.])',
        r'Группа\s+компаний\s+[«"]?([А-ЯЁ][а-яёА-ЯЁ\s\-]{2,20})[»"]?',
        r'застройщик\s+[«"]([^»"]{3,35})[»"]',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            name = match.group(1).strip().rstrip('.,;:')
            bad_words = ["планировали", "сдали", "сдаёт", "построил", "строит", "ввели"]
            if any(w in name.lower() for w in bad_words):
                continue
            if 3 < len(name) < 40 and name not in names:
                names.append(name)
    return names


def search_company_website(company_name: str, region: str) -> Optional[str]:
    queries = [
        f'"{company_name}" {region} официальный сайт',
        f"{company_name} {region} строительство",
    ]
    for query in queries:
        results = ddg_search(query, max_results=5)
        for r in results:
            url = r.get("href", "")
            snippet = r.get("body", "") + r.get("title", "")
            if not url or not is_valid_site(url):
                continue
            if company_name.lower()[:6] not in snippet.lower() and region.lower() not in snippet.lower():
                continue
            return extract_domain(url)
        time.sleep(0.5)
    return None


SPHERE_QUERIES = {
    "строительство частных домов": [
        "дом под ключ {region} официальный сайт строительная компания",
        "строительство частных домов {region} компания официальный сайт",
        "строим дом под ключ {region} цены проекты",
        "загородный дом под ключ {region} строительная компания",
        "строительство домов из кирпича газобетона {region}",
        "каркасные дома под ключ {region} официальный сайт",
        "дома из бруса под ключ {region} строительство",
        "индивидуальное строительство домов {region} под ключ",
        "газобетонный дом под ключ {region} строительная компания",
        "кирпичный дом под ключ {region} строительство",
        "коттедж под ключ {region} строительная компания официальный сайт",
        "монолитный дом под ключ {region} строительство",
        "строительство домов из газобетона {region} под ключ недорого",
        "частный дом {region} строительство под ключ проекты цены",
        "дом из пеноблоков под ключ {region} строительная компания",
        "SIP панели дом под ключ {region} строительство",
        "дом из арболита под ключ {region}",
        "строительство загородных домов {region} недорого под ключ",
        "дом из газосиликата под ключ {region} строительство",
        "строительство домов из бревна {region} под ключ сруб",
        "быстровозводимые дома {region} под ключ строительная компания",
        "дом из теплоблоков под ключ {region}",
    ],
    "коттеджные посёлки": [
        "коттеджный посёлок {region} от застройщика",
        "купить коттедж {region} официальный сайт",
        "коттеджи под ключ {region} строительная компания",
    ],
    "деревянное домостроение": [
        "деревянные дома {region} официальный сайт",
        "дома из бревна {region} строительство",
        "срубы домов {region} под ключ",
    ],
}

DEFAULT_SPHERE_QUERIES = [
    "застройщик многоквартирных домов {region} официальный сайт",
    "строительная компания новостройки {region} сайт застройщика",
    "жилой комплекс {region} от застройщика купить квартиру",
    "девелопер {region} новостройки квартиры продажа",
    "{sphere} {region} сдача квартир ЖК проекты",
    "новостройки {region} застройщик без посредников",
    "строительная компания {region} многоквартирные дома",
]

SPHERE_AGG_QUERIES = {
    "строительство частных домов": [
        "лучшие компании строительство домов под ключ {region} рейтинг",
        "топ строительных компаний частные дома {region} отзывы",
        "рейтинг строительных компаний частных домов {region} 2024 2025",
    ],
    "коттеджные посёлки": [
        "рейтинг застройщиков коттеджных посёлков {region}",
        "топ коттеджных посёлков {region} от застройщика",
    ],
}

DEFAULT_AGG_QUERIES = [
    "рейтинг застройщиков {region} 2024 2025 список",
    "топ застройщиков {region} многоквартирные дома",
    "список застройщиков {region} ЖК новостройки",
]


def get_direct_queries(region: str, sphere: str) -> List[str]:
    templates = SPHERE_QUERIES.get(sphere.lower(), DEFAULT_SPHERE_QUERIES)
    return [t.format(region=region, sphere=sphere) for t in templates]


def search_companies_direct(region: str, sphere: str, limit: int) -> List[dict]:
    queries = get_direct_queries(region, sphere)
    seen_domains = set()
    candidates = []

    for query in queries:
        if len(candidates) >= limit * 2:
            break
        results = ddg_search(query, max_results=10)
        for r in results:
            url = r.get("href", "")
            title = r.get("title", "")
            snippet = r.get("body", "")

            if not url or not is_valid_site(url):
                continue

            region_lower = region.lower()
            combined = (title + " " + snippet).lower()
            if region_lower not in combined:
                continue

            domain = extract_domain(url)
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            candidates.append({"name": title, "website": domain, "snippet": snippet})

        time.sleep(1)

    return candidates


def search_via_aggregators(region: str, sphere: str) -> List[dict]:
    templates = SPHERE_AGG_QUERIES.get(sphere.lower(), DEFAULT_AGG_QUERIES)
    agg_queries = [t.format(region=region, sphere=sphere) for t in templates]

    all_snippets = ""
    for query in agg_queries:
        results = ddg_search(query, max_results=5)
        for r in results:
            all_snippets += " " + r.get("body", "")
        time.sleep(1)

    company_names = extract_company_names_from_text(all_snippets)
    print(f"  Имён из агрегаторов: {len(company_names)} — {company_names[:5]}")

    found = []
    seen_domains = set()
    for name in company_names[:20]:
        website = search_company_website(name, region)
        if website and website not in seen_domains:
            seen_domains.add(website)
            found.append({"name": name, "website": website, "snippet": ""})
        time.sleep(0.5)

    return found


def enrich_company(company: dict, region: str, sphere: str) -> dict:
    name = company["name"]
    citations = count_citations(name)
    position_score = max(0, 10 - company.get("position", 5))
    traffic_estimate = position_score * 100 + citations * 50

    return {
        **company,
        "region": region,
        "sphere": sphere,
        "citations_count": citations,
        "reviews_count": 0,
        "traffic_estimate": traffic_estimate,
    }


def main():
    parser = argparse.ArgumentParser(description="Поиск строительных компаний")
    parser.add_argument("--region", required=True, help="Регион (напр. 'Самара')")
    parser.add_argument("--sphere", required=True, help="Сфера (напр. 'многоквартирные застройщики')")
    parser.add_argument("--limit", type=int, default=30, help="Максимум компаний")
    args = parser.parse_args()

    db.init_db()

    print(f"\nПоиск: {args.sphere} / {args.region} (лимит: {args.limit})")
    print("-" * 60)

    print("Этап 1: прямой поиск...")
    direct = search_companies_direct(args.region, args.sphere, args.limit)
    print(f"  Найдено напрямую: {len(direct)}")

    print("Этап 2: извлечение из агрегаторов...")
    via_agg = search_via_aggregators(args.region, args.sphere)
    print(f"  Найдено через агрегаторы: {len(via_agg)}")

    seen = set()
    all_candidates = []
    for c in direct + via_agg:
        if c["website"] not in seen:
            seen.add(c["website"])
            all_candidates.append(c)

    print(f"\nВсего уникальных кандидатов: {len(all_candidates)}")
    candidates = all_candidates[:args.limit]

    saved = 0
    for i, company in enumerate(candidates):
        print(f"  [{i+1}/{len(candidates)}] {company['name']} — {company['website']}")
        company["position"] = i + 1
        enriched = enrich_company(company, args.region, args.sphere)

        db.upsert_company(
            region=enriched["region"],
            sphere=enriched["sphere"],
            name=enriched["name"],
            website=enriched["website"],
            traffic_estimate=enriched["traffic_estimate"],
            citations_count=enriched["citations_count"],
            reviews_count=enriched["reviews_count"],
        )
        saved += 1
        time.sleep(0.3)

    print(f"\nСохранено/обновлено: {saved} компаний")
    print("\nТоп по цитируемости:")
    companies = db.get_companies(region=args.region, sphere=args.sphere, limit=20)
    for c in companies:
        print(f"  {c['citations_count']:3d} упом. | {c['reviews_count']:4d} отз. | {c['name']} ({c['website']})")


if __name__ == "__main__":
    main()
