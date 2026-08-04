# Skill: Генерация и перефраз контента компании

## Когда использовать

После сбора информации (шаг 2). Когда нужно создать HTML-страницу компании для конкретного целевого сайта.

## Сценарии

**Первичная генерация** (для нового сайта):
```bash
python execution/step3_generate_content.py --company-id 5 --target-site "https://example.com"
```

**Перефраз для второго сайта** (контент уже есть, нужна уникализация):
```bash
python execution/step3_generate_content.py --company-id 5 --target-site "https://example2.com"
```
Скрипт автоматически определит, что для `example2.com` контента нет, возьмёт базовый вариант и перефразирует его.

**Принудительная перегенерация:**
```bash
python execution/step3_generate_content.py --company-id 5 --target-site "https://example.com" --force
```

## Что происходит

1. Берём данные из `company_info` (результат шага 2)
2. Claude заполняет шаблон `builders.html` — подставляет переменные, удаляет пустые блоки
3. Если контент для `target-site` уже есть — Claude перефразирует текстовое содержимое (HTML-теги не трогает)
4. Результат сохраняется в `generated_content`

## Правила перефраза

- Сохранять точный смысл и все факты
- Менять структуру предложений и формулировки
- Не добавлять новые конкретные факты (цифры, даты, названия проектов)
- Без суперлативов
- Соблюдать целевой объём блоков по стандарту
  [directions/content-standards.md](../../directions/content-standards.md)
  (`about_company` 350–600, остальные блоки 150–300). Если исходный текст
  короче нижней границы — при перефразе можно расширить его общими нейтральными
  отраслевыми формулировками, не выдумывая конкретики

## Проверка результата

```bash
sqlite3 execution/data/companies.db \
  "SELECT company_id, target_site, length(html_content) as size, verified FROM generated_content;"
```

Экспорт HTML для просмотра:
```bash
sqlite3 execution/data/companies.db \
  "SELECT html_content FROM generated_content WHERE company_id=5 AND target_site='https://example.com';" > /tmp/preview.html
open /tmp/preview.html
```
