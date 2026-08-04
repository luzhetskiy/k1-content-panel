"""
Шаг 4 (локальный): Верификация сгенерированного HTML без API-ключа.
Программные проверки + опциональная сверка контактов с живым сайтом.

Запуск:
    python execution/step4_verify_local.py --target-site "https://vetonit-center.ru"
    python execution/step4_verify_local.py --target-site "https://vetonit-center.ru" --sphere "строительство частных домов"
    python execution/step4_verify_local.py --target-site "https://vetonit-center.ru" --company-id 20
    python execution/step4_verify_local.py --target-site "https://vetonit-center.ru" --sphere "строительство частных домов" --no-fetch
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(__file__))
import db

PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_0-9]+\}\}")
DOUBLE_V_RE = re.compile(r"\bв\s+в\s+", re.IGNORECASE)


def fetch_site_text(url: str) -> Optional[str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "iframe"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)[:30_000]
    except Exception as e:
        return None


def check_html(html: str, info: dict, site_text: Optional[str]) -> tuple[bool, list[str]]:
    """Возвращает (verified, issues)."""
    issues = []

    # 1. Незаменённые плейсхолдеры
    placeholders = PLACEHOLDER_RE.findall(html)
    if placeholders:
        uniq = list(dict.fromkeys(placeholders))
        issues.append(f"Остались плейсхолдеры: {', '.join(uniq)}")

    # 2. Двойное "в в" в тексте
    if DOUBLE_V_RE.search(html):
        issues.append('Двойное предложное "в в" в тексте')

    soup = BeautifulSoup(html, "html.parser")

    # 3. data-builder-name не пустой
    builder_div = soup.find(id="builder")
    gen_name = ""
    if builder_div:
        gen_name = (builder_div.get("data-builder-name") or "").strip()
        if not gen_name:
            issues.append("data-builder-name пустой")
    else:
        issues.append("Элемент #builder не найден")

    # 4. About-блоки: хотя бы 2 из 4 должны быть заполнены
    about_ids = ["builder-about-company", "builder-specialization",
                 "builder-projects-services", "builder-benefits"]
    filled = 0
    for aid in about_ids:
        el = soup.find(id=aid)
        if el and el.get_text(strip=True):
            filled += 1
    if filled < 2:
        issues.append(f"Мало заполненных about-блоков: {filled}/4")

    # 5. Контакты: хотя бы один контакт есть
    grid = soup.find(id="builder-contacts-grid")
    contact_items = grid.find_all("div", class_="partner-item") if grid else []
    if not contact_items:
        issues.append("Нет ни одного контактного блока")
    else:
        # Проверяем что у первого контакта есть хотя бы телефон или сайт
        first = contact_items[0]
        has_phone = bool(first.find(class_=lambda c: c and "builder-line-phone" in c))
        has_site = bool(first.find(class_=lambda c: c and "builder-line-site" in c))
        if not has_phone and not has_site:
            issues.append("В первом контакте нет ни телефона, ни сайта")

    # 6. Проверка контактного заголовка — не должен содержать "в в"
    title_el = soup.find(id="builder-contacts-title")
    if title_el:
        title_text = title_el.get_text(strip=True)
        if DOUBLE_V_RE.search(title_text):
            issues.append(f'Двойное "в" в заголовке контактов: "{title_text}"')

    # 7. Сверка с живым сайтом (если получилось загрузить)
    if site_text:
        # Имя компании должно встречаться на сайте
        if gen_name and gen_name.lower() not in site_text.lower():
            # Мягкая проверка: берём первое слово имени
            first_word = gen_name.split()[0] if gen_name.split() else ""
            if first_word and first_word.lower() not in site_text.lower():
                issues.append(
                    f'Название "{gen_name}" не найдено на сайте компании'
                )

        # Телефон из первого контакта должен быть на сайте
        contacts_raw = info.get("contacts") or []
        if isinstance(contacts_raw, str):
            try:
                contacts_raw = json.loads(contacts_raw)
            except Exception:
                contacts_raw = []
        if contacts_raw:
            phone = (contacts_raw[0].get("phone_tel") or "").strip()
            if phone:
                # Нормализуем: убираем + и сравниваем цифры
                phone_digits = re.sub(r"\D", "", phone)
                site_digits = re.sub(r"\D", "", site_text)
                # Ищем последние 10 цифр номера
                if len(phone_digits) >= 10 and phone_digits[-10:] not in site_digits:
                    issues.append(
                        f"Телефон {phone} не найден на сайте компании"
                    )

    verified = len(issues) == 0
    return verified, issues


def verify_company(company_id: int, target_site: str, fetch_live: bool = True) -> bool:
    contents = db.get_generated_content(company_id, target_site)
    if not contents:
        print(f"  [{company_id}] нет контента для {target_site} — пропускаем")
        return False

    content = contents[0]
    html = content.get("html_content") or ""
    if not html:
        print(f"  [{company_id}] пустой html_content — пропускаем")
        return False

    info = db.get_company_info(company_id)
    if not info:
        print(f"  [{company_id}] нет company_info — пропускаем")
        return False

    all_companies = db.get_companies(limit=10000)
    company = next((c for c in all_companies if c["id"] == company_id), None)
    website = company["website"] if company else ""
    name = info.get("builder_name") or company["name"] if company else f"id={company_id}"

    # Загружаем живой сайт
    site_text = None
    if fetch_live and website:
        site_text = fetch_site_text(website)

    verified, issues = check_html(html, info, site_text)

    site_status = f" (сайт: {'OK' if site_text else 'недоступен'})" if fetch_live else ""
    status = "OK" if verified else f"ПРОБЛЕМЫ ({len(issues)})"
    print(f"  [{company_id}] {name} — {status}{site_status}")
    for issue in issues:
        print(f"    ! {issue}")

    notes = "\n".join(issues) if issues else "Проверка пройдена"
    if fetch_live and not site_text:
        notes = (notes + "\n[WARN] Сайт компании недоступен — сверка с живым сайтом пропущена").strip()

    db.update_verification(company_id, target_site, verified, notes)
    return verified


def main():
    parser = argparse.ArgumentParser(description="Верификация HTML без API-ключа")
    parser.add_argument("--target-site", required=True)
    parser.add_argument("--company-id", type=int)
    parser.add_argument("--sphere", help="Фильтр по сфере")
    parser.add_argument("--region", help="Фильтр по региону")
    parser.add_argument("--no-fetch", action="store_true",
                        help="Не загружать живые сайты (только структурные проверки)")
    args = parser.parse_args()

    db.init_db()

    if args.company_id:
        company_ids = [args.company_id]
    else:
        companies = db.get_companies(
            region=args.region,
            sphere=args.sphere,
            limit=1000,
        )
        company_ids = [c["id"] for c in companies]

    target = args.target_site.rstrip("/")
    fetch_live = not args.no_fetch

    print(f"\nЦелевой сайт: {target}")
    print(f"Компаний к проверке: {len(company_ids)}")
    print(f"Сверка с живым сайтом: {'да' if fetch_live else 'нет'}")
    print("-" * 60)

    ok = 0
    for cid in company_ids:
        result = verify_company(cid, target, fetch_live)
        if result:
            ok += 1
        if fetch_live:
            time.sleep(0.5)  # вежливая пауза между запросами

    print(f"\nИтого: {ok}/{len(company_ids)} прошли верификацию")
    if ok < len(company_ids):
        print(f"Не прошли: {len(company_ids) - ok} — смотри детали выше")


if __name__ == "__main__":
    main()
