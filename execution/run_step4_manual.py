#!/usr/bin/env python3
"""
Шаг 4 (ручной): Верификация сгенерированного HTML.
Сравниваем данные в HTML с company_info из БД (без вызова API).
Проверяем:
  - нет незамещённых плейсхолдеров {{...}}
  - builder_name присутствует в HTML
  - city_name присутствует
  - phone_tel правильно вставлен в href
  - site_url правильно вставлен
  - нет очевидных артефактов подстановки
"""
import json
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "companies.db"
TARGET_SITE = "https://stroybaza-samara.ru"


def verify_html(html: str, info: dict, contacts: list) -> tuple:
    """Возвращает (verified: bool, issues: list[str])."""
    issues = []
    c1 = contacts[0] if contacts else {}

    # 1. Незамещённые плейсхолдеры
    remaining = re.findall(r'\{\{[A-Z_]+\}\}', html)
    if remaining:
        issues.append(f"Незамещённые плейсхолдеры: {', '.join(set(remaining))}")

    # 2. Имя компании
    builder_name = info.get("builder_name") or ""
    if builder_name and builder_name not in html:
        issues.append(f"Имя компании '{builder_name}' не найдено в HTML")

    # 3. Город
    city_name = info.get("city_name") or ""
    if city_name and city_name not in html:
        issues.append(f"Город '{city_name}' не найден в HTML")

    # 4. Телефон (если есть)
    phone_tel = c1.get("phone_tel") or ""
    if phone_tel:
        expected_href = f"tel:{phone_tel}"
        if expected_href not in html:
            issues.append(f"Телефон href '{expected_href}' не найден")

    # 5. Сайт компании (если есть)
    site_url = c1.get("site_url") or ""
    if site_url:
        if site_url not in html:
            issues.append(f"URL сайта '{site_url}' не найден в HTML")

    # 6. Ключевые HTML-блоки обязательно должны быть
    required_ids = ["builder", "builder-main-title", "builder-about",
                    "builder-contacts", "builder-contact-1"]
    for rid in required_ids:
        if f'id="{rid}"' not in html:
            issues.append(f"Отсутствует блок id=\"{rid}\"")

    # 7. Город в предложном падеже (в контактах)
    city_prep = info.get("city_prepositional") or ""
    if city_prep and city_prep not in html:
        issues.append(f"Город в предложном падеже '{city_prep}' не найден")

    # 8. Проверяем что data-builder-name заполнен корректно
    if builder_name and f'data-builder-name="{builder_name}"' not in html:
        issues.append(f"data-builder-name не совпадает с '{builder_name}'")

    verified = len(issues) == 0
    return verified, issues


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    rows = conn.execute("""
        SELECT
            c.id, c.name, c.website,
            ci.builder_name, ci.city_name, ci.city_prepositional,
            ci.contacts,
            gc.html_content
        FROM companies c
        JOIN company_info ci ON c.id = ci.company_id
        JOIN generated_content gc ON c.id = gc.company_id
        WHERE gc.target_site = ?
        ORDER BY c.id
    """, (TARGET_SITE,)).fetchall()

    print(f"Верификация {len(rows)} страниц → {TARGET_SITE}")
    print("-" * 60)

    total_ok = 0
    total_fail = 0

    for row in rows:
        info = dict(row)
        cid = info["id"]
        html = info["html_content"] or ""
        name = info.get("builder_name") or info.get("name") or ""

        contacts = []
        if info.get("contacts"):
            try:
                contacts = json.loads(info["contacts"])
            except Exception:
                pass

        verified, issues = verify_html(html, info, contacts)

        if verified:
            status = "VERIFIED"
            notes = "Автоматическая верификация пройдена: имя, город, контакты, структура HTML корректны."
            total_ok += 1
        else:
            status = "ISSUES"
            notes = "Проблемы:\n" + "\n".join(f"- {i}" for i in issues)
            total_fail += 1

        conn.execute("""
            UPDATE generated_content
            SET verified = ?, verification_notes = ?
            WHERE company_id = ? AND target_site = ?
        """, (int(verified), notes, cid, TARGET_SITE))
        conn.commit()

        icon = "✓" if verified else "✗"
        print(f"  {icon} id={cid:2d}  {name:<40}  {status}")
        if not verified:
            for issue in issues:
                print(f"        ⚠ {issue}")

    print(f"\nГотово: {total_ok} verified, {total_fail} issues")
    conn.close()


if __name__ == "__main__":
    main()
