"""
[УСТАРЕЛО — требует ANTHROPIC_API_KEY, не использовать]

Весь процесс выполняется ЛОКАЛЬНО на этой машине, без внешних LLM-API.
Актуальный шаг 4 — execution/step4_verify_local.py (программные проверки + сверка
с живым сайтом, без ключей). Этот файл оставлен только как исторический вариант.

Шаг 4 (устар.): Верификация сгенерированного контента субагентом.
Сравниваем факты в HTML с актуальным сайтом компании.

Запуск:
    python execution/step4_verify_content.py --company-id 5
    python execution/step4_verify_content.py --company-id 5 --target-site "https://example.com"
"""

import argparse
import json
import os
import sys
import time

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
import db

load_dotenv()

VERIFY_PROMPT = """Ты субагент-верификатор. Твоя задача — проверить фактическую точность HTML-страницы строительной компании, сравнив её с содержимым официального сайта.

HTML-страница для проверки:
{generated_html}

Содержимое официального сайта компании:
{site_content}

Проверь:
1. Название компании — совпадает ли?
2. Город/регион — корректный?
3. Контактные данные (телефон, email, адрес) — совпадают с сайтом?
4. Описание деятельности — соответствует реальной специализации?
5. Нет ли выдуманных фактов (год основания, число объектов, награды), которых нет на сайте?
6. Ссылка на сайт компании — корректная?

Верни ТОЛЬКО валидный JSON (без markdown-блоков):
{{
  "verified": true/false,
  "issues": [
    {{
      "field": "название поля",
      "problem": "описание проблемы",
      "generated_value": "что в HTML",
      "actual_value": "что на сайте"
    }}
  ],
  "notes": "общий комментарий верификатора"
}}

Если критических ошибок нет — verified: true, issues: [].
"""


def fetch_site_text(url: str) -> str:
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
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)[:15_000]
    except Exception as e:
        print(f"  [WARN] Не удалось загрузить сайт: {e}")
        return ""


def verify_with_claude(generated_html: str, site_content: str) -> Optional[dict]:
    import anthropic
    client = anthropic.Anthropic()

    prompt = VERIFY_PROMPT.format(
        generated_html=generated_html[:10_000],
        site_content=site_content,
    )

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        content = message.content[0].text.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()
        return json.loads(content)
    except json.JSONDecodeError as e:
        print(f"  [ERROR] JSON parse: {e}")
        return None
    except Exception as e:
        print(f"  [ERROR] Claude API: {e}")
        return None


def verify_content(company: dict, content: dict) -> None:
    target_site = content["target_site"]
    print(f"  Проверяю: company_id={company['id']} → {target_site}")

    site_content = fetch_site_text(company["website"])
    if not site_content:
        print("    [SKIP] Не удалось загрузить сайт компании")
        db.update_verification(
            company["id"], target_site, False,
            "Не удалось загрузить сайт компании для верификации"
        )
        return

    result = verify_with_claude(content["html_content"], site_content)
    if not result:
        print("    [FAIL] Claude не вернул результат верификации")
        return

    verified = result.get("verified", False)
    issues = result.get("issues", [])
    notes = result.get("notes", "")

    notes_full = notes
    if issues:
        issues_text = "\n".join(
            f"- [{i['field']}] {i['problem']}: '{i.get('generated_value','')}' → '{i.get('actual_value','')}'"
            for i in issues
        )
        notes_full = f"{notes}\n\nПроблемы:\n{issues_text}"

    db.update_verification(company["id"], target_site, verified, notes_full)

    status = "VERIFIED" if verified else "ISSUES FOUND"
    print(f"    {status}: {len(issues)} проблем")
    if issues:
        for issue in issues:
            print(f"      [{issue['field']}] {issue['problem']}")
    print(f"    {notes}")


def main():
    parser = argparse.ArgumentParser(description="Верификация контента")
    parser.add_argument("--company-id", type=int, required=True, help="ID компании")
    parser.add_argument("--target-site", help="Конкретный целевой сайт (опционально)")
    args = parser.parse_args()

    db.init_db()

    # Получаем данные компании
    all_companies = db.get_companies(limit=10000)
    company = next((c for c in all_companies if c["id"] == args.company_id), None)
    if not company:
        print(f"ERROR: компания id={args.company_id} не найдена")
        sys.exit(1)

    # Получаем контент для верификации
    contents = db.get_generated_content(args.company_id, args.target_site)
    if not contents:
        print(f"ERROR: нет сгенерированного контента для company_id={args.company_id}")
        sys.exit(1)

    print(f"\nКомпания: {company['name']} ({company['website']})")
    print(f"Контентов для проверки: {len(contents)}")
    print("-" * 60)

    for content in contents:
        verify_content(company, content)
        time.sleep(1)

    print("\nВерификация завершена.")


if __name__ == "__main__":
    main()
