# Skill: Управление тизерами компаний (Шаг 6)

## Что такое тизер

Тизер — карточка компании на сайте с адресом, телефоном, email, сайтом и ссылкой на полную страницу. Создаётся неактивным (is_active=false), публикуется вручную или отдельным PATCH-запросом.

Эндпоинт: `{site}/api/v1/addresses-services/`
Авторизация: тот же токен, что и для страниц — `SITE_API_TOKEN_{домен-без-ru}` из `.env`

---

## Перед созданием тизеров — узнать у пользователя

**Обязательно спросить в чате перед каждым запуском create:**

1. **`category`** — целочисленный ID категории тизеров на сайте
2. **`city`** — целочисленный ID города

Без этих параметров запуск невозможен. Не подставляй значения по умолчанию.

Если непонятно, какие ID использовать — сначала выполни `list`, чтобы посмотреть существующие тизеры:

```bash
python3 execution/step6_manage_teasers.py list --site "https://vetonit-center.ru"
```

---

## Действие 1: Создать тизеры (create)

### Предварительные условия

- Шаг 5 выполнен: страницы компаний созданы, в БД заполнен `page_url`
- Получены `category` и `city` от пользователя

### Команда

```bash
python3 execution/step6_manage_teasers.py create \
  --site "https://vetonit-center.ru" \
  --sphere "строительство частных домов" \
  --regions "Москва,Московская область,Подмосковье" \
  --category CATEGORY_ID \
  --city CITY_ID
```

### Параметры

| Параметр | Описание | Обязателен |
|---|---|---|
| `--site` | Базовый URL сайта | да |
| `--sphere` | Сфера деятельности (из БД) | да |
| `--regions` | Регионы через запятую | нет (без — все регионы) |
| `--region` | Один регион | нет |
| `--category` | ID категории (спросить у пользователя) | да |
| `--city` | ID города (спросить у пользователя) | да |
| `--location` | ID локации (default: 1) | нет |
| `--skip-no-page` | Пропускать компании без page_url | нет |

### Что подставляется в тизер из БД

| Поле API | Источник |
|---|---|
| `name` | `company_info.builder_name` |
| `slug` | из `page_url` страницы, без `/s/` и без `/` в конце |
| `address` | `contacts[0].address` |
| `phone` | `contacts[0].phone_text` |
| `email` | `contacts[0].email` |
| `website` | `contacts[0].site_url` |
| `description` | `contacts[0].working_hours` |
| `page_url` | относительный путь страницы, напр. `/s/rudom-moskva/` |
| `coordinates` | `["lat, lon"]` из `company_info.coordinates` (если есть) |
| `is_active` | `false` (всегда при создании) |
| `location` | 1 (default) |
| `category` | из аргумента `--category` |
| `city` | из аргумента `--city` |

---

## Действие 2: Список тизеров (list)

Используй, чтобы:
- узнать какие тизеры уже есть
- посмотреть доступные ID категорий и городов
- проверить результаты создания

```bash
python3 execution/step6_manage_teasers.py list \
  --site "https://vetonit-center.ru"

python3 execution/step6_manage_teasers.py list \
  --site "https://vetonit-center.ru" --limit 100 --offset 0
```

Вывод: таблица с ID, slug, name, is_active.

---

## Действие 3: Частичное обновление (patch)

Используй для:
- активации тизера: `is_active=true`
- смены категории или города
- исправления отдельных полей

```bash
# Активировать тизер
python3 execution/step6_manage_teasers.py patch \
  --site "https://vetonit-center.ru" \
  --id 42 \
  --set is_active=true

# Сменить категорию и город
python3 execution/step6_manage_teasers.py patch \
  --site "https://vetonit-center.ru" \
  --id 42 \
  --set category=5 city=3

# Несколько полей сразу
python3 execution/step6_manage_teasers.py patch \
  --site "https://vetonit-center.ru" \
  --id 42 \
  --set is_active=true category=5 city=2 location=1
```

Типы значений определяются автоматически: `true`/`false` → bool, цифры → int, остальное → string.

---

## Ошибки

| Ситуация | Что делать |
|---|---|
| `нет SITE_API_TOKEN_...` | Добавить токен в `.env` |
| `FAIL (HTTP 400)` | Проверить правильность `category` и `city` — запусти `list` |
| `SKIP: нет slug` | Компания не прошла шаг 5 — сначала создать страницу |
| `SKIP: нет company_info` | Компания не прошла шаг 2 — сначала скрейпинг |

---

## Типичный рабочий сценарий

```
1. Спросить у пользователя category и city
2. (опционально) list — посмотреть существующие тизеры
3. create — создать тизеры для партии компаний
4. list — убедиться, что тизеры появились
5. patch --set is_active=true — активировать при необходимости
```
