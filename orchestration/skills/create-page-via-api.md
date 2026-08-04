# Skill: Создание страницы через API

## Когда использовать

После верификации (шаг 4). Когда нужно создать страницу компании на целевом сайте как черновик.

## Предварительные условия

1. Шаг 3 выполнен — HTML контент сгенерирован
2. Шаг 4 выполнен — контент верифицирован
3. В `.env` прописаны `SITE_API_TOKEN_{домен}` и `SITE_PARENT_ID_{домен}`

## Команда запуска

```bash
python3 execution/step5_create_page.py --company-id 5 --site "https://stroybaza-samara.ru"
```

Флаг `--skip-verify-check` — пропустить проверку верификации (для тестов).

## API (проверено, работает)

- **Метод**: POST
- **Эндпоинт**: `{site}/api/v1/staticpages/`
- **Авторизация**: `Authorization: Token {SITE_API_TOKEN_...}`

### Payload

```json
{
  "title":            "Название компании — сфера в Регионе",
  "url":              "/s/nazvanie-kompanii-region/",
  "key":              "nazvanie-kompanii-region",
  "text":             "<html>... (без HTML-комментариев) ...",
  "published":        false,
  "meta_keywords":    "название, сфера, регион, ...",
  "meta_description": "Название — сфера в Регионе. Контакты, услуги, проекты.",
  "wide_view":        true,
  "use_editor":       false,
  "parent":           39
}
```

### Успешный ответ (HTTP 201)

```json
{
  "id": 50,
  "url": "/s/api-test-k1-parser",
  "published": false,
  "parent": 24,
  ...
}
```

## .env для каждого сайта

```
SITE_API_TOKEN_{домен-без-ru}=токен
SITE_PARENT_ID_{домен-без-ru}=числовой_id
```

Пример для stroybaza-samara.ru:
```
SITE_API_TOKEN_stroybaza-samara=41e548ec...
SITE_PARENT_ID_stroybaza-samara=39
```

## Быстрая проверка API нового сайта

Отредактировать `execution/test_api.py` (поменять `SITE`, `DOMAIN`) и запустить:
```bash
python3 execution/test_api.py
```
Ожидаемый результат: HTTP 201.

## Мониторинг созданных страниц

```bash
sqlite3 execution/data/companies.db \
  "SELECT gc.company_id, c.name, gc.target_site, gc.verified, gc.page_url
   FROM generated_content gc
   JOIN companies c ON c.id=gc.company_id
   ORDER BY gc.created_at DESC LIMIT 20;"
```

## Важно

- Страницы создаются с `published: false` — публикует менеджер вручную
- URL страницы: `/s/{slug}/` — slug из транслитерации имени компании + регион, обязательно с trailing slash
- `key`: тот же slug без префикса `/s/` — передаётся отдельным полем
- `use_editor: false`, `wide_view: true` — фиксированные значения
- HTML-комментарии из шаблона автоматически вырезаются перед отправкой
