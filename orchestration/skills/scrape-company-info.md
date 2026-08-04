# Skill: Поиск информации о компании

## Когда использовать

После составления списка (шаг 1). Когда нужно собрать подробную информацию с сайтов компаний по шаблону builders.html.

## Параметры запроса

Пользователь указывает:
- **Регион** и **сферу** — для выбора компаний из списка
- **Количество** — сколько компаний обработать

Или конкретную компанию по ID.

## Команды запуска

**По региону и сфере:**
```bash
python execution/step2_scrape_company.py --region "Москва" --sphere "застройщики" --count 10
```

**Конкретная компания:**
```bash
python execution/step2_scrape_company.py --company-id 5
```

## Что собирается

По шаблону [builders.html](../../builders.html):

| Поле | Описание |
|------|----------|
| `builder_name` | Название компании |
| `city_name` | Город (именительный падеж) |
| `city_prepositional` | Город в предложном падеже |
| `builder_label` | Лейбл/бейдж компании |
| `builder_logo_src` | URL логотипа |
| `builder_short_description` | Краткое описание |
| `builder_main_title` | Заголовок страницы |
| `about_company` | Описание компании |
| `specialization` | Специализация |
| `projects_services` | Услуги |
| `benefits` | Преимущества |
| `contacts` | Массив контактов (адрес, тел, email, часы) |

## Правила извлечения

- **Конкретные факты не выдумывать** — цифры, названия проектов, годы, гарантии
  берём только с сайта-источника
- Нейтральный язык, без суперлативов
- **Объём текстовых блоков** — по стандарту [directions/content-standards.md](../../directions/content-standards.md):
  `about_company` 350–600 символов, `specialization` / `projects_services` /
  `benefits` — по 150–300
- Если данных на сайте мало — не оставляем блок пустым, а добираем объём **общими
  нейтральными отраслевыми формулировками** (типовой процесс, материалы, этапы),
  не выдавая их за конкретные факты компании. Пустой блок — только если поле
  неприменимо по смыслу

## Проверка результата

```bash
sqlite3 execution/data/companies.db \
  "SELECT c.name, ci.builder_name, ci.city_name, ci.scraped_at FROM companies c JOIN company_info ci ON c.id=ci.company_id LIMIT 10;"
```
