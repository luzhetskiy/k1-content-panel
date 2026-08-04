"""
Шаг 2 (локальный, без API-ключей): подготовка описательного текста для страниц.

Процесс полностью локальный, на этой машине, без внешних LLM-API. Скрипт лишь
СКАЧИВАЕТ текст сайтов компаний в файлы; описание ("о компании", специализация,
услуги, преимущества) затем пишет оркестратор (агент), читая эти файлы, и
применяет его в company_info. Контакты, адрес, координаты и логотип уже получены
из выгрузки Яндекс.Карт (шаг 1) — здесь они не трогаются.

Типовой процесс:
    1) Скачать текст сайтов в файлы (по умолчанию — компании без описания):
       python execution/step2_scrape_company.py --region "Москва" --sphere "строительство домов"
       python execution/step2_scrape_company.py --company-id 172

    2) Агент читает файлы из --out и готовит JSON вида
       { "172": {"about_company": "...", "specialization": "...", ...}, ... }

    3) Применить тексты в company_info:
       python execution/step2_scrape_company.py --apply-text texts.json
"""

import argparse
import concurrent.futures
import json
import os
import sys
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(__file__))
import db

# Каталог по умолчанию для выгруженного текста сайтов.
DEFAULT_OUT = Path(__file__).parent / "data" / "site_text"

# Описательные поля, которые агент заполняет вручную и которые разрешено писать
# через --apply-text. Контакты/адрес/координаты/логотип/город сюда НЕ входят —
# они приходят из выгрузки Яндекс.Карт (шаг 1) и не перезаписываются.
ALLOWED_TEXT_FIELDS = (
    "builder_label", "builder_background_src", "builder_short_description",
    "builder_main_title", "about_company", "specialization",
    "projects_services", "benefits",
)


def fetch_html(url: str, timeout: int = 12) -> Optional[str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    return resp.text


def clean_to_text(raw_html: str) -> str:
    """Убираем скрипты/стили/навигацию, возвращаем читаемый текст сайта."""
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "header", "footer"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    text = soup.get_text(separator="\n", strip=True)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return f"TITLE: {title}\n\n" + "\n".join(lines)[:12_000]


def dump_site(company: dict, out_dir: Path) -> str:
    cid, name, site = company["id"], company["name"], company["website"]
    try:
        text = clean_to_text(fetch_html(site))
        (out_dir / f"{cid}.txt").write_text(f"# {name}\n# {site}\n\n{text}", encoding="utf-8")
        note = "" if len(text) > 200 else "  [!] мало текста — вероятно JS-сайт"
        return f"OK   [{cid}] {name} ({len(text)} симв.){note}"
    except Exception as e:
        (out_dir / f"{cid}.ERROR.txt").write_text(
            f"# {name}\n# {site}\n\nERROR: {e}", encoding="utf-8")
        return f"FAIL [{cid}] {name}: {str(e)[:80]}"


def apply_text(path: Path) -> None:
    """Применяет JSON { company_id: {field: text, ...} } в company_info."""
    data = json.loads(path.read_text(encoding="utf-8"))
    applied = 0
    for cid, fields in data.items():
        patch = {k: v for k, v in fields.items() if k in ALLOWED_TEXT_FIELDS}
        skipped = [k for k in fields if k not in ALLOWED_TEXT_FIELDS]
        if skipped:
            print(f"  [{cid}] пропущены не-текстовые поля: {', '.join(skipped)}")
        if patch:
            db.patch_company_info(int(cid), patch)
            applied += 1
            print(f"  [{cid}] обновлено полей: {len(patch)}")
    print(f"\nПрименено к компаниям: {applied}")


def main():
    parser = argparse.ArgumentParser(
        description="Шаг 2 (локальный): выгрузка текста сайтов и применение описаний")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--region", help="Фильтр по региону")
    group.add_argument("--company-id", type=int, help="ID конкретной компании")
    parser.add_argument("--sphere", help="Фильтр по сфере (с --region)")
    parser.add_argument("--count", type=int, default=25, help="Количество компаний (с --region)")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="Каталог для выгруженного текста сайтов")
    parser.add_argument("--apply-text", metavar="FILE",
                        help="Применить JSON {id: {field: text}} в company_info и выйти")
    args = parser.parse_args()

    db.init_db()

    # Режим применения готовых текстов.
    if args.apply_text:
        apply_text(Path(args.apply_text))
        return

    # Режим выгрузки текста сайтов.
    if args.company_id:
        companies = [c for c in db.get_companies(limit=10000) if c["id"] == args.company_id]
        if not companies:
            print(f"ERROR: компания с id={args.company_id} не найдена")
            sys.exit(1)
    else:
        if not args.region:
            print("ERROR: укажите --region, --company-id или --apply-text")
            sys.exit(1)
        companies = db.get_companies(
            region=args.region,
            sphere=args.sphere,
            limit=args.count,
            only_without_text=True,
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nВыгрузка текста сайтов: {len(companies)} компаний → {out_dir}")
    print("-" * 60)

    ok = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(lambda c: dump_site(c, out_dir), companies):
            print(res)
            if res.startswith("OK"):
                ok += 1

    print(f"\nВыгружено: {ok}/{len(companies)}")
    print("Дальше: агент читает файлы из каталога выше, готовит JSON с описаниями")
    print("и применяет его:  python execution/step2_scrape_company.py --apply-text texts.json")


if __name__ == "__main__":
    main()
