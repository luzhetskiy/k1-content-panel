# Skill: Составление списка компаний

## Когда использовать

Когда нужно найти строительные компании по заданному региону и сфере деятельности и добавить их в базу данных.

## Параметры запроса

Пользователь должен сообщить:
- **Регион** — см. [directions/regions.md](../../directions/regions.md)
- **Сфера деятельности** — см. [directions/spheres.md](../../directions/spheres.md)
- **Лимит** — максимальное число компаний (по умолчанию 30)

## Команда запуска

```bash
python execution/step1_search_companies.py --region "РЕГИОН" --sphere "СФЕРА" --limit ЛИМИТ
```

**Пример:**
```bash
python execution/step1_search_companies.py --region "Москва" --sphere "застройщики" --limit 30
```

## Что происходит

1. Tavily Search ищет компании по нескольким запросам
2. Фильтрует агрегаторы (Avito, ЦИАН, ДомКлик и т.д.)
3. Считает цитируемость каждой компании в поиске
4. Собирает количество отзывов на Яндекс.Картах и 2GIS
5. Вычисляет эвристику посещаемости
6. Сохраняет/обновляет в SQLite (`execution/data/companies.db`)

## Проверка результата

```bash
sqlite3 execution/data/companies.db \
  "SELECT name, website, citations_count, reviews_count FROM companies WHERE region='Москва' AND sphere='застройщики' ORDER BY citations_count DESC LIMIT 10;"
```

## Важно

- Нужен `TAVILY_API_KEY` в `.env`
- Скрипт можно запускать повторно — данные обновляются (upsert)
- Компании без сайта автоматически исключаются
- Ранжирование по `citations_count` (число упоминаний в поиске)
