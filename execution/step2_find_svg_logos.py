"""
Поиск SVG-логотипов на сайтах компаний, у которых нет builder_logo_src и builder_logo_svg.
Проверяет header/nav страницы: если находит svg-логотип — сохраняет в builder_logo_svg,
если находит img-логотип — сохраняет в builder_logo_src.

Запуск:
    python execution/step2_find_svg_logos.py --sphere "строительство частных домов" --dry-run
    python execution/step2_find_svg_logos.py --sphere "строительство частных домов"
    python execution/step2_find_svg_logos.py --company-id 76
"""

import argparse
import os
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(__file__))
import db

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}
TIMEOUT = 20


def is_logo_candidate(tag) -> bool:
    attrs = " ".join([
        tag.get("id") or "",
        " ".join(tag.get("class", [])),
        tag.get("alt") or "",
        tag.get("aria-label") or "",
    ]).lower()
    return bool(re.search(r"logo|лого", attrs))


def is_inline_svg(svg) -> bool:
    """True если SVG самодостаточен (inline path-данные) и подходит для логотипа."""
    if svg.get("aria-hidden") == "true":
        return False

    # SVG с внешними спрайтами — не работают на чужом домене
    for use in svg.find_all("use"):
        href = use.get("href") or use.get("xlink:href") or ""
        if href and not href.startswith("#") and ".svg" in href:
            return False

    # Слишком маленький размер — иконка (проверяем только если атрибут задан явно)
    try:
        h = float(re.sub(r"[^\d.]", "", svg.get("height", "0") or "0"))
        w = float(re.sub(r"[^\d.]", "", svg.get("width", "0") or "0"))
        if (h and h < 16) or (w and w < 24):
            return False
        # viewBox без явных w/h: если viewBox тоже мал — иконка
        vb = svg.get("viewbox") or svg.get("viewBox") or ""
        if not h and not w and vb:
            parts = re.split(r"[\s,]+", vb.strip())
            if len(parts) == 4:
                vb_w, vb_h = float(parts[2]), float(parts[3])
                if vb_h < 16 or vb_w < 24:
                    return False
    except (ValueError, TypeError):
        pass

    return bool(svg.find("path") or svg.find("g") or svg.find("circle")
                or svg.find("rect") or svg.find("polygon") or svg.find("text"))


def find_logo_in_scope(scope, base_url: str) -> tuple[str, str]:
    """Возвращает (logo_src, logo_svg)."""
    logo_containers = [t for t in scope.find_all(True) if is_logo_candidate(t)]
    search_in = logo_containers if logo_containers else [scope]

    # Сначала ищем img-логотип
    for container in search_in:
        for img in container.find_all("img"):
            src = img.get("src", "")
            if not src:
                continue
            # Пропускаем загруженные пользователем медиа-файлы (партнёрские логотипы в контенте)
            if re.search(r"/wp-content/uploads/|/upload/|/media/uploads/", src):
                continue
            if re.search(r"logo|лого", src, re.IGNORECASE) or is_logo_candidate(img):
                return urljoin(base_url, src), ""

    # Потом inline SVG (без внешних спрайтов)
    for container in search_in:
        for svg in container.find_all("svg"):
            if len(str(svg)) < 30:
                continue
            if is_inline_svg(svg):
                return "", str(svg)

    return "", ""


def fetch_html(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"  [ERROR] {url}: {e}")
        return None


def find_logo(website: str) -> tuple[str, str]:
    html = fetch_html(website)
    if not html:
        return "", ""

    soup = BeautifulSoup(html, "html.parser")
    base_url = website.rstrip("/")

    # Ищем в header/nav
    scope = (
        soup.find("header")
        or soup.find(id=re.compile(r"header", re.I))
        or soup.find(class_=re.compile(r"header", re.I))
        or soup.find("nav")
    )
    logo_src, logo_svg = find_logo_in_scope(scope or soup, base_url)

    # Если не нашли — ищем по всему документу через явные logo-контейнеры
    if not logo_src and not logo_svg:
        logo_containers = [t for t in soup.find_all(True) if is_logo_candidate(t)]
        for container in logo_containers:
            logo_src, logo_svg = find_logo_in_scope(container, base_url)
            if logo_src or logo_svg:
                break

    return logo_src, logo_svg


def process(company_id: int, website: str, builder_name: str, dry_run: bool) -> str:
    print(f"  [{company_id}] {builder_name} ({website}) ...", end=" ", flush=True)
    logo_src, logo_svg = find_logo(website)

    if logo_src:
        print(f"IMG: {logo_src[:80]}")
        if not dry_run:
            db.patch_company_info(company_id, {"builder_logo_src": logo_src})
        return "img"
    elif logo_svg:
        print(f"SVG ({len(logo_svg)} симв.): {logo_svg[:70].replace(chr(10), ' ')}...")
        if not dry_run:
            db.patch_company_info(company_id, {"builder_logo_svg": logo_svg})
        return "svg"
    else:
        print("не найден")
        return "none"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sphere", help="Фильтр по сфере")
    parser.add_argument("--company-id", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db.init_db()

    if args.company_id:
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT c.id, c.website, ci.builder_name FROM companies c "
                "JOIN company_info ci ON ci.company_id = c.id WHERE c.id = ?",
                (args.company_id,)
            ).fetchall()
        rows = [dict(r) for r in rows]
    else:
        rows = db.query_companies_no_logo(args.sphere)

    label = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{label}Компаний без логотипа: {len(rows)}\n" + "-" * 70)

    stats = {"img": 0, "svg": 0, "none": 0}
    for row in rows:
        status = process(row["id"], row["website"], row["builder_name"], args.dry_run)
        stats[status] += 1
        time.sleep(1)

    print(f"\n{label}Итого: IMG={stats['img']}  SVG={stats['svg']}  не найдено={stats['none']}")


if __name__ == "__main__":
    main()
