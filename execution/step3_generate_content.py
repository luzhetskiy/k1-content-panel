"""
[УСТАРЕЛО — требует ANTHROPIC_API_KEY, не использовать]

Весь процесс выполняется ЛОКАЛЬНО на этой машине, без внешних LLM-API.
Актуальный шаг 3 — execution/step3_fill_template.py (заполняет шаблон напрямую,
без ключей). Этот файл оставлен только как исторический вариант на Claude API.

Шаг 3 (устар.): Генерация HTML-страницы компании по шаблону builders.html.
При повторном запуске для того же сайта — перефразирует контент.

Запуск:
    python execution/step3_generate_content.py --company-id 5 --target-site "https://example.com"
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
import db

load_dotenv()

TEMPLATE_PATH = Path(__file__).parent.parent / "builders.html"

REPHRASE_PROMPT = """Ты редактор текстового контента. Перефразируй следующие текстовые блоки о строительной компании для публикации на другом сайте.

Правила:
- Сохраняй точный смысл и все факты без изменений
- Меняй структуру предложений и формулировки
- Не используй суперлативы: "лучший", "самый", "100%"
- Не добавляй новые факты
- Язык: русский

Блоки для перефраза (JSON):
{blocks}

Верни ТОЛЬКО валидный JSON с теми же ключами и перефразированными значениями. Без markdown-блоков.
"""

FILL_TEMPLATE_PROMPT = """Ты генерируешь HTML для страницы строительной компании по шаблону.

Информация о компании (JSON):
{info_json}

Шаблон HTML:
{template}

Инструкции:
1. Замени все {{ПЕРЕМЕННЫЕ}} в шаблоне данными из JSON
2. Если данные отсутствуют (пустая строка) — удали соответствующий HTML-блок или тег
3. Не добавляй факты, которых нет в JSON
4. Не изменяй CSS-классы и структуру HTML
5. Верни ТОЛЬКО финальный HTML без объяснений и без markdown-блоков
"""


def load_template() -> str:
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Шаблон не найден: {TEMPLATE_PATH}")
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def fill_template_with_claude(info: dict, template: str) -> Optional[str]:
    import anthropic
    client = anthropic.Anthropic()

    # Убираем raw_html из данных перед отправкой
    info_clean = {k: v for k, v in info.items() if k not in ("raw_html", "id", "company_id", "scraped_at")}
    if isinstance(info_clean.get("contacts"), list):
        info_clean["contacts"] = info_clean["contacts"]
    info_json = json.dumps(info_clean, ensure_ascii=False, indent=2)

    prompt = FILL_TEMPLATE_PROMPT.format(info_json=info_json, template=template)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        result = message.content[0].text.strip()
        # Убираем markdown-обёртку если есть
        if result.startswith("```"):
            result = result.split("```")[1]
            if result.startswith("html"):
                result = result[4:]
        return result.strip()
    except Exception as e:
        print(f"  [ERROR] Claude API: {e}")
        return None


def rephrase_with_claude(html_content: str) -> Optional[str]:
    import anthropic
    client = anthropic.Anthropic()

    # Ищем текстовые блоки в about-секции
    patterns = {
        "about_company": r'(<div[^>]*class="[^"]*about[^"]*"[^>]*>)(.*?)(</div>)',
        "specialization": r'(<p[^>]*id="specialization[^"]*"[^>]*>)(.*?)(</p>)',
    }

    # Проще — просим Claude перефразировать всё текстовое содержимое
    prompt = f"""Перефразируй все текстовые блоки внутри HTML (заголовки, параграфы, описания).
Сохраняй HTML-теги, атрибуты, классы и структуру неизменными.
Меняй только текстовое содержимое внутри тегов.
Не добавляй новые факты. Сохраняй смысл.

HTML:
{html_content[:20000]}

Верни ТОЛЬКО готовый HTML без пояснений и без markdown-блоков."""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        result = message.content[0].text.strip()
        if result.startswith("```"):
            result = result.split("```")[1]
            if result.startswith("html"):
                result = result[4:]
        return result.strip()
    except Exception as e:
        print(f"  [ERROR] Claude rephrase: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Генерация HTML контента компании")
    parser.add_argument("--company-id", type=int, required=True, help="ID компании")
    parser.add_argument("--target-site", required=True, help="URL целевого сайта (напр. https://example.com)")
    parser.add_argument("--force", action="store_true", help="Перегенерировать даже если уже есть")
    args = parser.parse_args()

    db.init_db()

    # Проверяем существующий контент
    existing = db.get_generated_content(args.company_id, args.target_site)

    if existing and not args.force:
        print(f"Контент для company_id={args.company_id} / {args.target_site} уже есть.")
        print("Перефразирую для нового сайта...")
        base_html = existing[0]["html_content"]
        rephrased = rephrase_with_claude(base_html)
        if rephrased:
            db.save_generated_content(args.company_id, args.target_site, rephrased)
            print(f"  OK: перефраз сохранён для {args.target_site}")
        else:
            print("  FAIL: перефраз не удался")
        return

    # Получаем информацию о компании
    info = db.get_company_info(args.company_id)
    if not info:
        print(f"ERROR: нет scraped-данных для company_id={args.company_id}. Сначала запусти step2.")
        sys.exit(1)

    template = load_template()

    print(f"Генерирую HTML для company_id={args.company_id} / {args.target_site}...")
    html = fill_template_with_claude(info, template)

    if not html:
        print("FAIL: не удалось сгенерировать HTML")
        sys.exit(1)

    db.save_generated_content(args.company_id, args.target_site, html)
    print(f"  OK: HTML сохранён ({len(html)} символов)")

    # Показываем первые строки
    preview = html[:300].replace("\n", " ")
    print(f"  Preview: {preview}...")


if __name__ == "__main__":
    main()
