# Полнота данных строителей — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Довести карточки и тизеры строителей до полноты: график работы и логотип берутся из выгрузки Яндекса, логотип сохраняется с верным расширением, скрейпер перестаёт промахиваться и подсовывать заглушки, а шаблон всегда оставляет либо картинку, либо название компании.

**Architecture:** Источник правды для достоверных полей — `CompanyCandidate`; новый модуль `app/companies/info.py` собирает из него `CompanyInfo` и умеет пересобрать её для уже существующей компании (иначе старые компании не увидят новых колонок). Логотип разрешается цепочкой «выгрузка → скрейпинг сайта → запасная подпись», расширение файла определяется по `Content-Type` ответа. `fill_builder_template` перестаёт удалять элементы блока логотипа и прячет лишний — это рвёт петлю «шаблон синхронизируется с собранной страницы, у которой элемент уже вырезан».

**Tech Stack:** те же, что и во всём бэкенде проекта (FastAPI, SQLAlchemy, Alembic, Celery, pytest, BeautifulSoup, openpyxl). Фронтенд не затрагивается.

**Спека:** `directions/2026-09-12-builders-data-quality-design.md`

---

## Структура файлов

```
execution/backend/
  alembic/versions/<new>_candidate_working_hours_logo.py  Create: company_candidates += working_hours, logo_url
  app/companies/import_xlsx.py        Modify: COLUMNS += График/Логотип/Все телефоны, ParsedRow += 2 поля
  app/companies/imports.py            Modify: upsert новых полей
  app/companies/info.py               Create: build_info_from_candidate / refresh_info_from_candidate
  app/companies/logo.py               Modify: LogoCandidate, lazy-атрибуты, фильтр по контейнеру, inline svg, cookie-заглушка
  app/companies/template.py           Modify: прятать лишний элемент логотипа вместо удаления
  app/companies/reference.py          Modify: builder-logo/builder-logo-text в контракте, вырезание комментариев
  app/companies/builder.py            Modify: refresh info, _upload_logo, _relocate_logo, тизер, city_prepositional, rstrip base_url
  app/models/company.py               Modify: CompanyCandidate += working_hours, logo_url
  app/api/company_batches.py          Modify: использует info.build_info_from_candidate
  app/sites/client.py                 Modify: create_teaser/update_teaser += description
  app/seed.py                         Modify: DEFAULT_PROMPTS["builder_text"] += city_prepositional
  fix_builder_reference_logo_text.py  Create: разовый скрипт правки эталонных страниц
  reset_builder_logos.py              Create: разовый скрипт обнуления builder_logo_src

  tests/test_companies_import_xlsx.py  Modify: заголовок фикстуры + тест новых колонок
  tests/test_companies_imports.py      Modify: upsert новых полей
  tests/test_companies_info.py         Create: build/refresh
  tests/test_companies_logo.py         Modify: LogoCandidate + новые сценарии поиска
  tests/test_companies_template.py     Modify: оба элемента логотипа остаются в выдаче
  tests/test_companies_reference.py    Modify: контракт эталона
  tests/test_companies_builder.py      Modify: logo_filename, цепочка логотипа, тизер, city_prepositional
  tests/test_sites_client.py           Modify: description у тизера
  tests/test_api_company_batches.py    Modify: info собирается через новый модуль
  tests/test_fix_builder_reference.py  Create: тест разового скрипта
```

Фронтенд не меняется: график работы и логотип в интерфейсе панели не показываются, а пересборка запускается существующей кнопкой «Пересобрать» (`POST /api/companies/{id}/retry`).

## Как запускать

```bash
cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution
docker compose run --rm --no-deps backend pytest -q          # весь бэкенд-регресс, SQLite in-memory
docker compose run --rm --no-deps backend pytest -q tests/test_companies_logo.py -v   # один файл
docker compose up -d postgres redis                           # для проверки миграции на реальном Postgres
docker compose run --rm backend alembic upgrade head
```

Текущий head Alembic — `b5a9c64497ea` (`b5a9c64497ea_rename_images_regenerating_to_.py`).

---

### Task 1: Импорт xlsx — колонки «График», «Логотип», «Все телефоны»

**Files:**
- Modify: `execution/backend/app/companies/import_xlsx.py:14-32`, `:38-62`, `:150-175`
- Test: `execution/backend/tests/test_companies_import_xlsx.py:10-20`

- [ ] **Step 1: Добавить новые колонки в заголовок тестовой фикстуры**

Колонки дописываются **в конец** заголовка — тогда существующие строки тестов
(по 16 значений) продолжают работать: `openpyxl` дополняет короткие строки
`None` до ширины листа, а `_get` возвращает `None` для индекса за пределами
строки.

```python
def _make_workbook(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Запрос", "Название", "Категории", "Регион", "Город", "Полный адрес",
              "Мобильные", "Немобильные", "Сайт", "Email с сайта компании", "График",
              "Широта", "Долгота", "Оценок", "Отзывов", "Рейтинг",
              "Логотип", "Все телефоны"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

- [ ] **Step 2: Написать падающий тест**

Добавить в `tests/test_companies_import_xlsx.py`:

```python
def test_parses_working_hours_logo_and_falls_back_to_all_phones():
    """«График» и «Логотип» есть в выгрузке Яндекса (95% и 78% строк), но до
    сентября 2026 не импортировались. «Все телефоны» — третий запасной
    источник телефона после «Немобильные»/«Мобильные»."""
    data = _make_workbook([
        ["застройщик", "ООО Дом", "Стройка", "Самарская область", "Самара",
         "ул. Ленина 1", "", "", "https://dom-samara.ru", "info@dom-samara.ru",
         "пн-пт 09:00–18:00", 53.2, 50.1, 10, 5, 4.8,
         "https://avatars.mds.yandex.net/get-altay/1/XXXL",
         "+7 846 111-22-33 | +7 846 111-22-34"],
    ])
    row = parse_workbook(data)[0]
    assert row.working_hours == "пн-пт 09:00–18:00"
    assert row.logo_url == "https://avatars.mds.yandex.net/get-altay/1/XXXL"
    assert row.phone == "+7 846 111-22-33"


def test_file_without_new_columns_still_imports():
    """Новые колонки необязательные: REQUIRED_KEYS не расширяется, файл без
    них разбирается как раньше."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Название", "Категории", "Регион", "Город", "Сайт"])
    ws.append(["ООО Дом", "Стройка", "Самарская область", "Самара", "https://dom.ru"])
    buf = io.BytesIO()
    wb.save(buf)
    row = parse_workbook(buf.getvalue())[0]
    assert row.working_hours == ""
    assert row.logo_url == ""
```

- [ ] **Step 3: Запустить тесты и убедиться, что падают**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_import_xlsx.py -v`
Expected: FAIL — `AttributeError: 'ParsedRow' object has no attribute 'working_hours'`

- [ ] **Step 4: Добавить колонки в `COLUMNS` и поля в `ParsedRow`**

```python
COLUMNS = {
    "name": "Название",
    "category": "Категории",
    "region": "Регион",
    "city": "Город",
    "address": "Полный адрес",
    "phone_mobile": "Мобильные",
    "phone_landline": "Немобильные",
    "phone_all": "Все телефоны",
    "site": "Сайт",
    "email": "Email с сайта компании",
    "working_hours": "График",
    "logo_url": "Логотип",
    "lat": "Широта",
    "lon": "Долгота",
    "ratings": "Оценок",
    "reviews": "Отзывов",
    "rating": "Рейтинг",
    "yandex_card": "Карточка организации",
}
```

`REQUIRED_KEYS` не трогать — новые колонки необязательны.

В `ParsedRow` после `email`:

```python
    working_hours: str = ""
    logo_url: str = ""
```

- [ ] **Step 5: Заполнить новые поля в `parse_workbook`**

Заменить строку получения телефона:

```python
            phone = (_get(row, header, "phone_landline")
                     or _get(row, header, "phone_mobile")
                     or _get(row, header, "phone_all") or "")
```

И добавить в конструктор `ParsedRow` (рядом с `email=`):

```python
                working_hours=str(_get(row, header, "working_hours") or "").strip(),
                logo_url=str(_get(row, header, "logo_url") or "").split("|")[0].strip(),
```

Срез по `|` у логотипа — тот же контракт источника, что у «Фото» и «Сайт»:
в выгрузке множественные значения разделяются вертикальной чертой.

- [ ] **Step 6: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_import_xlsx.py -v`
Expected: PASS, все тесты файла зелёные

- [ ] **Step 7: Коммит**

```bash
git add execution/backend/app/companies/import_xlsx.py execution/backend/tests/test_companies_import_xlsx.py
git commit -m "feat: импорт колонок График/Логотип/Все телефоны из выгрузки Яндекса"
```

---

### Task 2: Поля `working_hours` и `logo_url` у кандидата + миграция

**Files:**
- Modify: `execution/backend/app/models/company.py:56-60`
- Modify: `execution/backend/app/companies/imports.py:56-71`
- Create: `execution/backend/alembic/versions/<generated>_candidate_working_hours_logo.py`
- Test: `execution/backend/tests/test_companies_imports.py`

- [ ] **Step 1: Написать падающий тест upsert'а**

Добавить в `tests/test_companies_imports.py`:

```python
def test_import_stores_working_hours_and_logo(db_session):
    data = _make_workbook([
        ["застройщик", "ООО Дом", "Стройка", "Самарская область", "Самара",
         "ул. Ленина 1", "", "+7 846 000-00-00", "https://dom.ru", "i@dom.ru",
         "ежедневно, 09:00–18:00", 53.2, 50.1, 10, 5, 4.8,
         "https://avatars.mds.yandex.net/get-altay/1/XXXL", ""],
    ])
    import_file(db_session, data, "yandex.xlsx", uploaded_by_id=None)
    candidate = db_session.query(CompanyCandidate).one()
    assert candidate.working_hours == "ежедневно, 09:00–18:00"
    assert candidate.logo_url == "https://avatars.mds.yandex.net/get-altay/1/XXXL"


def test_reimport_updates_working_hours_and_logo(db_session):
    """Повторная загрузка того же файла — это и есть способ добрать новые
    колонки у уже импортированных кандидатов (см. §7 спеки)."""
    old = _make_workbook([
        ["застройщик", "ООО Дом", "Стройка", "Самарская область", "Самара",
         "ул. Ленина 1", "", "+7 846 000-00-00", "https://dom.ru", "i@dom.ru",
         "", 53.2, 50.1, 10, 5, 4.8, "", ""],
    ])
    import_file(db_session, old, "old.xlsx", uploaded_by_id=None)
    new = _make_workbook([
        ["застройщик", "ООО Дом", "Стройка", "Самарская область", "Самара",
         "ул. Ленина 1", "", "+7 846 000-00-00", "https://dom.ru", "i@dom.ru",
         "пн-пт 10:00–19:00", 53.2, 50.1, 10, 5, 4.8, "https://logo/1", ""],
    ])
    import_file(db_session, new, "new.xlsx", uploaded_by_id=None)
    candidate = db_session.query(CompanyCandidate).one()
    assert candidate.working_hours == "пн-пт 10:00–19:00"
    assert candidate.logo_url == "https://logo/1"
```

Если в файле теста ещё нет `_make_workbook`/импортов — скопировать хелпер из
`tests/test_companies_import_xlsx.py` (с заголовком из Task 1, Step 1) и
добавить `from app.models.company import CompanyCandidate`.

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_imports.py -v`
Expected: FAIL — `AttributeError: type object 'CompanyCandidate' has no attribute 'working_hours'`

- [ ] **Step 3: Добавить колонки в модель**

В `app/models/company.py`, класс `CompanyCandidate`, после поля `email`:

```python
    working_hours: Mapped[str] = mapped_column(Text, default="")
    logo_url: Mapped[str] = mapped_column(Text, default="")
```

`Text`, а не `String(n)`, сознательно. Значения приходят из чужого файла, длину
которого мы не контролируем: в разобранной выгрузке самый длинный «График» —
110 символов, но гарантий сверху нет, а цена промаха несоразмерна. `import_file`
(`app/companies/imports.py:73-82`) ловит любое исключение коммита, откатывает
транзакцию целиком и помечает импорт `failed` с текстом «не удалось сохранить
компании — проверьте данные файла»: одна длинная строка убила бы весь файл, а
понять причину по этому сообщению нельзя. Тесты идут на SQLite, который
`VARCHAR(n)` не проверяет, — такой отказ не поймал бы ни один тест, только прод.
Прецедент уже был: `phone` расширяли миграцией
`9864d416847d_widen_company_candidate_phone`.

`Text` уже импортирован в модуль (его использует `CompanyImport.error_message`).

- [ ] **Step 4: Копировать новые поля в upsert**

В `app/companies/imports.py`, в цикле `for row in rows:`, после `existing.email = row.email`:

```python
        existing.working_hours = row.working_hours
        existing.logo_url = row.logo_url
```

- [ ] **Step 5: Запустить тест и убедиться, что проходит**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_imports.py -v`
Expected: PASS

- [ ] **Step 6: Сгенерировать миграцию**

```bash
cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution
docker compose up -d postgres
docker compose run --rm backend alembic revision -m "candidate working hours logo"
```

В созданном файле проверить `down_revision = 'b5a9c64497ea'` (текущий head) и
заполнить тело:

```python
def upgrade() -> None:
    op.add_column("company_candidates",
                  sa.Column("working_hours", sa.Text(),
                            nullable=False, server_default=""))
    op.add_column("company_candidates",
                  sa.Column("logo_url", sa.Text(),
                            nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("company_candidates", "logo_url")
    op.drop_column("company_candidates", "working_hours")
```

`server_default=""` обязателен: в таблице на проде 5214 строк, а колонки
объявлены `NOT NULL` — без дефолта `ALTER TABLE` упадёт.

- [ ] **Step 7: Применить миграцию на реальном Postgres**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm backend alembic upgrade head`
Expected: `Running upgrade b5a9c64497ea -> <new>, candidate working hours logo`

- [ ] **Step 8: Коммит**

```bash
git add execution/backend/app/models/company.py execution/backend/app/companies/imports.py execution/backend/alembic/versions/ execution/backend/tests/test_companies_imports.py
git commit -m "feat: working_hours и logo_url у кандидата компании"
```

---

### Task 3: Модуль `app/companies/info.py`

**Files:**
- Create: `execution/backend/app/companies/info.py`
- Modify: `execution/backend/app/api/company_batches.py:142-170`
- Test: `execution/backend/tests/test_companies_info.py` (создать)

- [ ] **Step 1: Написать падающий тест**

```python
from app.companies.info import (
    build_info_from_candidate, refresh_info_from_candidate, split_phone,
)
from app.models.company import Company, CompanyCandidate, CompanyInfo


def _candidate(**over):
    base = dict(site_key="dom.ru", website_raw="https://dom.ru", name="ООО Дом",
                region_raw="Самарская область", category_raw="Стройка", city="Самара",
                address="ул. Ленина 1", phone="+7 846 000-00-00", email="i@dom.ru",
                working_hours="пн-пт 09:00–18:00", logo_url="https://logo/1.png",
                lat=53.195873, lon=50.100199)
    base.update(over)
    return CompanyCandidate(**base)


def test_build_info_carries_working_hours_and_logo(db_session):
    candidate = _candidate()
    db_session.add(candidate)
    company = Company(site_id=1, site_key="dom.ru", name="ООО Дом")
    db_session.add(company)
    db_session.commit()

    info = build_info_from_candidate(company, candidate)
    assert info.contacts[0]["working_hours"] == "пн-пт 09:00–18:00"
    assert info.builder_logo_src == "https://logo/1.png"
    assert info.coordinates == "53.195873, 50.100199"


def test_refresh_pulls_new_columns_into_existing_info(db_session):
    """Ради этого модуль и заводится: CompanyInfo заполняется один раз при
    создании партии, поэтому у собранных до доработки компаний в contacts
    нет working_hours, а в builder_logo_src — логотипа из выгрузки."""
    candidate = _candidate()
    db_session.add(candidate)
    db_session.flush()
    company = Company(site_id=1, site_key="dom.ru", name="ООО Дом",
                     candidate_id=candidate.id)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    refresh_info_from_candidate(db_session, company)
    assert company.info.contacts[0]["working_hours"] == "пн-пт 09:00–18:00"
    assert company.info.builder_logo_src == "https://logo/1.png"


def test_refresh_keeps_scraped_logo_when_candidate_has_none(db_session):
    """Пустой логотип в выгрузке не должен затирать найденный скрейпингом —
    иначе каждая пересборка заново ходила бы на сайт компании."""
    candidate = _candidate(logo_url="")
    db_session.add(candidate)
    db_session.flush()
    company = Company(site_id=1, site_key="dom.ru", name="ООО Дом",
                     candidate_id=candidate.id)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id,
                               builder_logo_src="/media/uploads/service-img/x.png"))
    db_session.commit()

    refresh_info_from_candidate(db_session, company)
    assert company.info.builder_logo_src == "/media/uploads/service-img/x.png"


def test_refresh_is_noop_for_company_without_candidate(db_session):
    """Мигрированные из CLI компании кандидата не имеют (candidate_id=NULL)."""
    company = Company(site_id=1, site_key="dom.ru", name="ООО Дом", candidate_id=None)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="Старое имя"))
    db_session.commit()

    refresh_info_from_candidate(db_session, company)
    assert company.info.builder_name == "Старое имя"


def test_split_phone_keeps_department_note_out_of_href():
    """Живой дефект: на stroybaza-moscow.ru страница «Академик Строй» отдаёт
    href="tel:+7 (495) 106-30-40 Отдел продаж". Пометка нужна человеку в
    подписи, но в href попадать не должна."""
    href, text = split_phone("+7 (495) 106-30-40 Отдел продаж")
    assert href == "+74951063040"
    assert text == "+7 (495) 106-30-40 Отдел продаж"


def test_split_phone_takes_first_of_several_numbers():
    href, text = split_phone("+7 (495) 157-12-25,+7 (495) 502-79-69 Отдел продаж")
    assert href == "+74951571225"
    assert text == "+7 (495) 157-12-25"


def test_split_phone_returns_empty_href_for_unparseable_value():
    """Номер, который не привести к 10-11 цифрам, — не номер: пустой href
    лучше ссылки, по которой нельзя позвонить."""
    href, text = split_phone("звоните через сайт")
    assert href == ""
    assert text == "звоните через сайт"


def test_split_phone_converts_domestic_eight_prefix_for_href():
    """8-800 — российская междугородняя запись. В href нужна международная
    форма: +8 не код страны, по такой ссылке не дозвониться. В подписи
    исходная запись остаётся как есть — компания печатает её именно так."""
    href, text = split_phone("8 (800) 333-11-11,+7 (495) 150-11-11")
    assert href == "+78003331111"
    assert text == "8 (800) 333-11-11"


def test_split_phone_keeps_free_text_label_intact():
    """Запятая делит номера только тогда, когда в ячейке номера. Если номер
    не распознан, это свободный текст — резать его по запятой значит терять
    половину фразы."""
    href, text = split_phone("звоните с 9 до 18, номер уточняйте на сайте")
    assert href == ""
    assert text == "звоните с 9 до 18, номер уточняйте на сайте"


def test_build_info_splits_phone(db_session):
    candidate = _candidate(phone="+7 (846) 277-06-05 Приёмная")
    db_session.add(candidate)
    company = Company(site_id=1, site_key="dom.ru", name="ООО Дом")
    db_session.add(company)
    db_session.commit()

    info = build_info_from_candidate(company, candidate)
    assert info.contacts[0]["phone_tel"] == "+78462770605"
    assert info.contacts[0]["phone_text"] == "+7 (846) 277-06-05 Приёмная"


def test_refresh_does_not_wipe_city_prepositional(db_session):
    """city_prepositional и builder_logo_alt источника у кандидата не имеют —
    обновление не должно их обнулять."""
    candidate = _candidate()
    db_session.add(candidate)
    db_session.flush()
    company = Company(site_id=1, site_key="dom.ru", name="ООО Дом",
                     candidate_id=candidate.id)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, city_prepositional="Самаре"))
    db_session.commit()

    refresh_info_from_candidate(db_session, company)
    assert company.info.city_prepositional == "Самаре"
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_info.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.companies.info'`

- [ ] **Step 3: Создать модуль**

```python
"""Сборка CompanyInfo из кандидата выгрузки Яндекс.Карт.

Вынесено из app/api/company_batches.py, потому что те же поля нужно уметь
обновлять у уже существующей компании при пересборке (CompanyBuilder), а
импортировать API-модуль в Celery-задачу нельзя.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.company import Company, CompanyCandidate, CompanyInfo
from app.sites.client import normalize_phone


_PHONE_LEAD = re.compile(r"^[\d\s()+\-]+")


def split_phone(raw: str) -> tuple[str, str]:
    """(href, подпись) для блока контактов.

    В выгрузке телефон приходит с пометкой отдела («+7 (495) 106-30-40 Отдел
    продаж», 700 строк из 5214) и иногда несколькими номерами через запятую.
    Раньше сырое значение уходило и в href, и в подпись — на проде 15 из 63
    собранных компаний получили ссылку вида
    `href="tel:+7 (495) 106-30-40 Отдел продаж"` (проверено на живой странице
    stroybaza-moscow.ru/s/akademik-stroy-moskva/). В href должен попадать
    только набираемый номер; пометка остаётся в подписи, она информативна.

    Обрезка по разделителю уместна только когда номер распознан: тогда в
    ячейке действительно список номеров (1091 строка из 5407). Если номер не
    распознан, в ячейке свободный текст, и резать его по запятой значит
    терять половину фразы — подпись возвращается целиком.

    Ведущая «8» в href приводится к «7»: 8-800 — российская междугородняя
    запись, а `tel:+8...` никуда не звонит, потому что +8 не код страны
    (285 номеров из 5405). В подписи исходная запись сохраняется — компания
    печатает её именно так. `normalize_phone` при этом не трогаем: её
    контракт (11 цифр с 7 или 8) задан API тизера.
    """
    raw = raw or ""
    first = re.split(r"[,;/|]", raw, maxsplit=1)[0].strip()
    match = _PHONE_LEAD.match(first)
    digits = normalize_phone(match.group(0)) if match else ""
    if not digits:
        return "", raw.strip()
    if digits.startswith("8"):
        digits = "7" + digits[1:]
    return f"+{digits}", first


def _fields_from_candidate(candidate: CompanyCandidate) -> dict:
    phone_tel, phone_text = split_phone(candidate.phone)
    contact = {
        "address": candidate.address,
        "phone_tel": phone_tel,
        "phone_text": phone_text,
        "email": candidate.email,
        "working_hours": candidate.working_hours,
        "site_url": candidate.website_raw,
        "site_text": candidate.site_key,
    }
    coordinates = (f"{candidate.lat:.6f}, {candidate.lon:.6f}"
                  if candidate.lat is not None and candidate.lon is not None else "")
    return {
        "builder_name": candidate.name,
        "city_name": candidate.city,
        "builder_logo_src": candidate.logo_url,
        "contacts": [contact] if any(contact.values()) else [],
        "address": candidate.address,
        "coordinates": coordinates,
    }


def build_info_from_candidate(company: Company, candidate: CompanyCandidate) -> CompanyInfo:
    """CompanyInfo с достоверными фактами из выгрузки — это то, что билдер
    (app/companies/builder.py) считает YANDEX_INFO_FIELDS и никогда не даёт
    RouterAI переписывать."""
    return CompanyInfo(company_id=company.id, **_fields_from_candidate(candidate))


def refresh_info_from_candidate(db: Session, company: Company) -> None:
    """Повторно берёт достоверные поля из кандидата перед пересборкой.

    city_prepositional и builder_logo_alt не трогаются: источника у кандидата
    для них нет (первое заполняет модель, см. builder._apply_ai_fields), и
    обновление их бы обнулило.
    """
    if company.candidate_id is None:      # мигрированные из CLI — кандидата нет
        return
    candidate = db.get(CompanyCandidate, company.candidate_id)
    if candidate is None or company.info is None:
        return
    for field, value in _fields_from_candidate(candidate).items():
        # Логотип из выгрузки перекрывает всё; если в выгрузке его нет —
        # оставляем найденный скрейпингом, иначе каждая пересборка заново
        # ходила бы на сайт компании за тем же файлом.
        if field == "builder_logo_src" and not value:
            continue
        setattr(company.info, field, value)
    db.commit()
```

- [ ] **Step 4: Запустить тест и убедиться, что проходит**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_info.py -v`
Expected: PASS, 9 тестов

- [ ] **Step 5: Переключить API партий на новый модуль**

В `app/api/company_batches.py` удалить функцию `_company_info_from_candidate`
целиком, добавить импорт:

```python
from app.companies.info import build_info_from_candidate
```

и заменить её вызов в `_add_company_from_candidate`:

```python
def _add_company_from_candidate(db: Session, batch: CompanyBatch, candidate: CompanyCandidate) -> Company:
    company = _company_from_candidate(batch, candidate)
    db.add(company)
    db.flush()
    db.add(build_info_from_candidate(company, candidate))
    return company
```

Удалить из импортов `CompanyInfo`, если он больше нигде в файле не используется.

Run: `grep -n "CompanyInfo" execution/backend/app/api/company_batches.py`
Expected: пусто (иначе импорт оставить)

- [ ] **Step 6: Запустить регресс API партий**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_api_company_batches.py -v`
Expected: PASS

- [ ] **Step 7: Коммит**

```bash
git add execution/backend/app/companies/info.py execution/backend/app/api/company_batches.py execution/backend/tests/test_companies_info.py
git commit -m "feat: app/companies/info.py — сборка и обновление CompanyInfo из кандидата"
```

---

### Task 4: Пересборка подтягивает свежие поля кандидата

**Files:**
- Modify: `execution/backend/app/companies/builder.py:53-72`
- Test: `execution/backend/tests/test_companies_builder.py`

- [ ] **Step 1: Написать падающий тест**

```python
def test_build_refreshes_info_from_candidate(db_session, site, company):
    """Компания собрана до доработки импорта: в contacts нет working_hours.
    Пересборка обязана добрать его из кандидата, иначе график работы не
    появится ни на странице, ни в тизере."""
    from app.models.company import CompanyCandidate, CompanyInfo

    _seed_prompts(db_session)
    candidate = CompanyCandidate(
        site_key="dom.ru", website_raw="https://dom.ru", name="ООО Дом",
        region_raw="Самарская область", category_raw="Стройка", city="Самара",
        address="ул. Ленина 1", phone="+7 846 000-00-00", email="i@dom.ru",
        working_hours="пн-пт 09:00–18:00", logo_url="")
    db_session.add(candidate)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.flush()
    company.candidate_id = candidate.id
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    _builder(db_session, company, site).build()

    assert company.status == "published"
    assert company.info.contacts[0]["working_hours"] == "пн-пт 09:00–18:00"
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_builder.py::test_build_refreshes_info_from_candidate -v`
Expected: FAIL — `KeyError: 'working_hours'`

- [ ] **Step 3: Вызвать обновление в начале сборки**

В `app/companies/builder.py` добавить импорт:

```python
from app.companies.info import refresh_info_from_candidate
```

и в `build()` сразу после `info = self._require_info()`:

```python
            info = self._require_info()
            refresh_info_from_candidate(self.db, self.company)
```

- [ ] **Step 4: Запустить тест и убедиться, что проходит**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_builder.py -v`
Expected: PASS, весь файл зелёный

- [ ] **Step 5: Коммит**

```bash
git add execution/backend/app/companies/builder.py execution/backend/tests/test_companies_builder.py
git commit -m "feat: пересборка компании обновляет данные из выгрузки"
```

---

### Task 5: Логотип сохраняется с верным расширением, `data:` отбрасывается

**Files:**
- Modify: `execution/backend/app/companies/builder.py:27-33`, `:142-158`
- Test: `execution/backend/tests/test_companies_builder.py:73-75`

- [ ] **Step 1: Написать падающие тесты**

Существующий тест `test_logo_filename_uses_company_prefix` заменить на:

```python
def test_logo_filename_keeps_extension_of_real_content():
    """Раньше имя всегда было .webp независимо от содержимого: сайт отдавал
    SVG с Content-Type image/webp, и браузер такой логотип не рисовал
    (9 битых логотипов на проде)."""
    assert logo_filename(7, ".svg") == "cp-company-7-logo.svg"
    assert logo_filename(7, ".jpg") == "cp-company-7-logo.jpg"
```

И добавить:

```python
def test_relocate_logo_uses_extension_from_content_type(db_session, site, company):
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               builder_logo_src="https://dom.ru/logo"))
    db_session.commit()

    site_client = Mock(
        create_page=Mock(return_value={"id": 99, "url": "/s/ooo-dom-samara/"}),
        create_teaser=Mock(return_value=555),
        upload_file=Mock(return_value="/media/uploads/service-img/cp-company-7-logo.svg"))
    response = Mock(content=b"<svg/>", headers={"Content-Type": "image/svg+xml"})
    response.raise_for_status = Mock()
    with patch("app.companies.builder.requests.get", return_value=response):
        _builder(db_session, company, site, site_client=site_client).build()

    assert site_client.upload_file.call_args.args[1] == "cp-company-7-logo.svg"


def test_relocate_logo_drops_data_uri_placeholder(db_session, site, company):
    """lazy-load-сайты кладут в src прозрачный data:image/svg+xml. Раньше он
    уезжал в страницу как логотип — прозрачный прямоугольник вместо
    картинки (компании 280 и 312 на проде)."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(
        company_id=company.id, builder_name="ООО Дом",
        builder_logo_src="data:image/svg+xml;utf8,<svg><rect/></svg>"))
    db_session.commit()

    with patch("app.companies.builder.requests.get") as get:
        _builder(db_session, company, site).build()

    get.assert_not_called()
    assert company.info.builder_logo_src == ""


def test_relocate_logo_rejects_non_image_content_type(db_session, site, company):
    """Заглушка антибота или HTML страницы ошибки вместо картинки — заливать
    нельзя, уходим на запасную подпись названием."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               builder_logo_src="https://dom.ru/logo"))
    db_session.commit()

    site_client = Mock(
        create_page=Mock(return_value={"id": 99, "url": "/s/ooo-dom-samara/"}),
        create_teaser=Mock(return_value=555), upload_file=Mock())
    response = Mock(content=b"<html>", headers={"Content-Type": "text/html"})
    response.raise_for_status = Mock()
    with patch("app.companies.builder.requests.get", return_value=response):
        _builder(db_session, company, site, site_client=site_client).build()

    site_client.upload_file.assert_not_called()
    assert company.info.builder_logo_src == ""
```

Убедиться, что `CompanyInfo` импортирован в начале файла теста (он уже есть
в списке импортов).

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_builder.py -v`
Expected: FAIL — `TypeError: logo_filename() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Переписать `logo_filename` и добавить таблицу расширений**

```python
# Расширение определяется по Content-Type ответа, а не по URL: у логотипов
# Яндекса (avatars.mds.yandex.net/.../XXXL) расширения в пути нет вовсе.
_EXT_BY_CONTENT_TYPE = {
    "image/svg+xml": ".svg",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def logo_filename(company_id: int, ext: str) -> str:
    """Префикс cp-company- — та же причина, что и у cp-article- в
    app/articles/builder.py: не пересекаться со старой CLI-схемой
    (execution/step3_fill_template.py грузит логотипы как logo-{name})."""
    return f"cp-company-{company_id}-logo{ext}"
```

- [ ] **Step 4: Переписать `_relocate_logo` и выделить `_upload_logo`**

```python
    def _upload_logo(self, info: CompanyInfo, data: bytes, ext: str) -> None:
        info.builder_logo_src = self.site_client.upload_file(
            data, logo_filename(self.company.id, ext), SERVICE_IMG_DIR)
        self.db.commit()

    def _relocate_logo(self, info: CompanyInfo) -> None:
        """Внешний логотип перезаливается на целевой сайт — иначе карточка
        зависит от чужого хостинга. Уже локальные пути (/media/...) не трогаем."""
        src = info.builder_logo_src or ""
        # data: — не логотип, а lazy-load-заглушка (прозрачный <svg>):
        # скачать её нечем, а оставить как есть означает прозрачный
        # прямоугольник вместо логотипа на странице.
        if src.startswith("data:"):
            info.builder_logo_src = ""
            self.db.commit()
            return
        if not src or src.startswith("/"):
            return
        try:
            response = requests.get(src, timeout=12)
            response.raise_for_status()
        except requests.RequestException:
            logger.warning(
                "не удалось перезалить логотип компании %s (%s) — оставляю "
                "внешнюю ссылку как есть", self.company.id, src)
            return
        ctype = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        ext = _EXT_BY_CONTENT_TYPE.get(ctype)
        if ext is None:
            logger.warning("логотип компании %s: неизвестный тип %r — пропускаю",
                           self.company.id, ctype)
            info.builder_logo_src = ""
            self.db.commit()
            return
        self._upload_logo(info, response.content, ext)
```

- [ ] **Step 5: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_builder.py -v`
Expected: PASS

- [ ] **Step 6: Коммит**

```bash
git add execution/backend/app/companies/builder.py execution/backend/tests/test_companies_builder.py
git commit -m "fix: логотип сохраняется с расширением по Content-Type, data:-заглушки отбрасываются"
```

---

### Task 6: `LogoCandidate` вместо строки-URL

**Files:**
- Modify: `execution/backend/app/companies/logo.py:51-77`
- Modify: `execution/backend/app/companies/builder.py:129-140`
- Test: `execution/backend/tests/test_companies_logo.py`

Тип меняется заранее, отдельной задачей: inline-`<svg>` (Task 9) вернуть
строкой-URL нечем, а менять контракт вместе с новым поведением — значит
смешивать в одном коммите рефакторинг и фичу.

- [ ] **Step 1: Переписать существующие тесты под новый тип**

В `tests/test_companies_logo.py` заменить импорт и все `find_logo_url(...) == "..."`
на `find_logo(...).url == "..."`:

```python
from app.companies.logo import LogoCandidate, fetch_company_logo, find_logo
```

Например, первый тест становится:

```python
def test_find_logo_finds_img_with_logo_in_src_inside_header():
    html = """
    <header>
      <div class="nav"><img src="/images/other.png"></div>
      <img src="/static/logo-dom.png" alt="">
    </header>
    <img src="/static/decoy-logo.png">
    """
    assert find_logo(html, "https://dom.ru").url == "https://dom.ru/static/logo-dom.png"
```

Run: `grep -n "find_logo_url" execution/backend/tests/test_companies_logo.py`
Expected: пусто после правки

- [ ] **Step 2: Добавить тест на пустой результат**

```python
def test_logo_candidate_is_falsy_when_nothing_found():
    """Билдер проверяет результат в булевом контексте — пустой кандидат не
    должен считаться найденным логотипом."""
    assert not LogoCandidate()
    assert LogoCandidate(url="https://dom.ru/logo.png")
    assert LogoCandidate(svg_markup="<svg/>")
```

- [ ] **Step 3: Запустить тесты и убедиться, что падают**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_logo.py -v`
Expected: FAIL — `ImportError: cannot import name 'LogoCandidate'`

- [ ] **Step 4: Ввести тип и переименовать функции**

В `app/companies/logo.py` добавить импорт `from dataclasses import dataclass` и тип:

```python
@dataclass(frozen=True)
class LogoCandidate:
    """Логотип бывает двух видов: ссылка на файл (её качаем и перезаливаем)
    и inline-<svg> прямо в разметке шапки (его заливаем как .svg)."""

    url: str = ""
    svg_markup: str = ""

    def __bool__(self) -> bool:
        return bool(self.url or self.svg_markup)
```

Переименовать `find_logo_url` → `find_logo` и вернуть кандидата:

```python
def find_logo(html: str, base_url: str) -> LogoCandidate:
    soup = BeautifulSoup(html, "html.parser")
    scope = (
        soup.find("header")
        or soup.find(id=re.compile(r"header", re.I))
        or soup.find(class_=re.compile(r"header", re.I))
        or soup.find("nav")
    )
    logo_src = _find_img_in_scope(scope or soup, base_url)
    if logo_src:
        return LogoCandidate(url=logo_src)

    for container in [t for t in soup.find_all(True) if _is_logo_candidate(t)]:
        logo_src = _find_img_in_scope(container, base_url)
        if logo_src:
            return LogoCandidate(url=logo_src)
    return LogoCandidate()


def fetch_company_logo(website: str) -> LogoCandidate:
    try:
        response = requests.get(website, headers=_HEADERS, timeout=_TIMEOUT_SECONDS,
                                allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException:
        return LogoCandidate()
    return find_logo(response.text, website.rstrip("/"))
```

- [ ] **Step 5: Адаптировать билдер**

В `app/companies/builder.py`, `_find_logo`:

```python
    def _find_logo(self, info: CompanyInfo) -> None:
        """Логотип из выгрузки Яндекса («Логотип», 78% строк) приезжает в
        builder_logo_src через refresh_info_from_candidate. Скрейпинг шапки
        сайта компании — запасной источник для остальных."""
        if info.builder_logo_src:
            return
        candidate = self.logo_fn(self.company.website)
        if candidate.url:
            info.builder_logo_src = candidate.url
            self.db.commit()
```

Докстринг-упоминание «В выгрузке Яндекс.Карт колонки „Логотип" нет» удалить —
оно неверно и ввело в заблуждение при разборе прода.

В `_builder()` в `tests/test_companies_builder.py` поправить дефолт фейка:

```python
        logo_fn=logo_fn or Mock(return_value=LogoCandidate()),
```

добавив импорт `from app.companies.logo import LogoCandidate`.

- [ ] **Step 6: Обновить докстринг модуля `logo.py`**

Первый абзац файла заменить на:

```python
"""Поиск логотипа на сайте компании-строителя — запасной источник, когда в
выгрузке Яндекс.Карт колонка «Логотип» пуста (это ~22% строк). Порт
find_logo/find_logo_in_scope из execution/step2_find_svg_logos.py, дополненный
ленивой загрузкой и inline-<svg>: все картинки строителей по требованию
загружаются в service-img как файлы (см. CompanyBuilder._upload_logo), а не
как встроенная в страницу SVG-разметка."""
```

- [ ] **Step 7: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_logo.py tests/test_companies_builder.py -v`
Expected: PASS

- [ ] **Step 8: Коммит**

```bash
git add execution/backend/app/companies/logo.py execution/backend/app/companies/builder.py execution/backend/tests/test_companies_logo.py execution/backend/tests/test_companies_builder.py
git commit -m "refactor: find_logo возвращает LogoCandidate вместо строки"
```

---

### Task 7: Ленивая загрузка — читать `data-src`/`srcset`, игнорировать `data:`

**Files:**
- Modify: `execution/backend/app/companies/logo.py:38-49`
- Test: `execution/backend/tests/test_companies_logo.py`

- [ ] **Step 1: Написать падающие тесты**

```python
def test_find_logo_reads_lazy_attribute_when_src_is_placeholder():
    """sip-lider.ru: в src заглушка lazy.svg, настоящий логотип в data-lazy."""
    html = """
    <header>
      <img src="/local/templates/s/images/lazy.svg"
           data-lazy="/local/templates/s/images/logo_animate.svg" alt="">
    </header>
    """
    assert find_logo(html, "https://sip-lider.ru").url == \
        "https://sip-lider.ru/local/templates/s/images/logo_animate.svg"


def test_find_logo_ignores_data_uri_placeholder_in_src():
    """project-me.ru: в src прозрачный data:image/svg+xml, реальный файл в
    data-src. Раньше побеждала заглушка."""
    html = """
    <header>
      <img class="component-logo-img" alt="Logo"
           src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg'></svg>"
           data-src="/img/logo.png">
    </header>
    """
    assert find_logo(html, "https://project-me.ru").url == "https://project-me.ru/img/logo.png"


def test_find_logo_returns_nothing_when_only_data_uri_available():
    html = """
    <header>
      <img class="logo" src="data:image/svg+xml;utf8,<svg></svg>">
    </header>
    """
    assert find_logo(html, "https://dom.ru").url == ""


def test_find_logo_takes_first_url_from_srcset():
    html = """
    <header>
      <img class="logo" srcset="/img/logo.png 1x, /img/logo@2x.png 2x">
    </header>
    """
    assert find_logo(html, "https://dom.ru").url == "https://dom.ru/img/logo.png"
```

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_logo.py -v`
Expected: FAIL — первый тест возвращает `https://sip-lider.ru/local/templates/s/images/lazy.svg`

- [ ] **Step 3: Ввести чтение ленивых атрибутов**

В `app/companies/logo.py` добавить перед `_find_img_in_scope`:

```python
# Порядок важен: src проверяется первым, и только если там не заглушка.
_SRC_ATTRS = ("src", "data-src", "data-lazy", "data-lazy-src", "data-original")
_SRCSET_ATTRS = ("srcset", "data-srcset")


def _img_src(img) -> str:
    """Адрес картинки с учётом ленивой загрузки. data:-URI не адрес, а
    встроенная заглушка-прозрачность — для логотипа бесполезна."""
    for attr in _SRC_ATTRS:
        value = (img.get(attr) or "").strip()
        if value and not value.startswith("data:"):
            return value
    for attr in _SRCSET_ATTRS:
        value = (img.get(attr) or "").strip()
        if not value:
            continue
        first = value.split(",")[0].strip().split(" ")[0]
        if first and not first.startswith("data:"):
            return first
    return ""
```

- [ ] **Step 4: Использовать `_img_src` в поиске**

```python
def _find_img_in_scope(scope, base_url: str) -> str:
    containers = [t for t in scope.find_all(True) if _is_logo_candidate(t)]
    search_in = containers if containers else [scope]
    for container in search_in:
        for img in container.find_all("img"):
            src = _img_src(img)
            if not src or _SKIP_SRC.search(src):
                continue
            if re.search(r"logo|лого", src, re.IGNORECASE) or _is_logo_candidate(img):
                return urljoin(base_url, src)
    return ""
```

- [ ] **Step 5: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_logo.py -v`
Expected: PASS

- [ ] **Step 6: Коммит**

```bash
git add execution/backend/app/companies/logo.py execution/backend/tests/test_companies_logo.py
git commit -m "fix: поиск логотипа читает data-src/srcset и игнорирует data:-заглушки"
```

---

### Task 8: Фильтр по контейнеру вместо чёрного списка путей

**Files:**
- Modify: `execution/backend/app/companies/logo.py:25`, `:38-49`
- Test: `execution/backend/tests/test_companies_logo.py:29-39`

- [ ] **Step 1: Переписать тест старого фильтра**

`_SKIP_SRC` режет `/wp-content/uploads/` — а на WordPress-сайтах именно там
лежит настоящий логотип (skvlasov.ru: `/wp-content/uploads/2024/11/logo.png`).
Заменить `test_find_logo_url_skips_user_uploaded_media_even_with_logo_in_name` на:

```python
def test_find_logo_finds_wordpress_uploaded_logo():
    """skvlasov.ru: логотип лежит в /wp-content/uploads/. Старый фильтр по
    пути резал его вместе с партнёрскими картинками — теперь фильтруем по
    контейнеру, а не по каталогу."""
    html = """
    <header>
      <img src="/wp-content/uploads/2024/11/logo.png" alt="">
    </header>
    """
    assert find_logo(html, "https://skvlasov.ru").url == \
        "https://skvlasov.ru/wp-content/uploads/2024/11/logo.png"


def test_find_logo_skips_images_inside_partner_block():
    """Партнёрские логотипы в контенте страницы — не логотип самой компании."""
    html = """
    <div class="partners-list">
      <img src="/wp-content/uploads/2024/logo-partner.png" alt="Logo">
    </div>
    """
    assert find_logo(html, "https://dom.ru").url == ""
```

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_logo.py -v`
Expected: FAIL — первый тест возвращает `""`

- [ ] **Step 3: Заменить фильтр**

Удалить `_SKIP_SRC` и добавить:

```python
# Логотипы партнёров/клиентов в контенте страницы — не логотип самой
# компании, даже если в имени файла есть "logo". Раньше это отсекалось по
# каталогу (/wp-content/uploads/), но там же лежит и настоящий логотип
# WordPress-сайта, поэтому фильтруем по контейнеру.
_SKIP_CONTAINER = re.compile(r"partner|client|brand|portfolio|gallery|slider", re.I)


def _in_skipped_container(tag) -> bool:
    for parent in tag.parents:
        blob = " ".join([parent.get("id") or "", " ".join(parent.get("class", []))])
        if _SKIP_CONTAINER.search(blob):
            return True
    return False
```

- [ ] **Step 4: Использовать новый фильтр в поиске**

```python
def _find_img_in_scope(scope, base_url: str) -> str:
    containers = [t for t in scope.find_all(True) if _is_logo_candidate(t)]
    search_in = containers if containers else [scope]
    for container in search_in:
        for img in container.find_all("img"):
            src = _img_src(img)
            if not src or _in_skipped_container(img):
                continue
            if re.search(r"logo|лого", src, re.IGNORECASE) or _is_logo_candidate(img):
                return urljoin(base_url, src)
    return ""
```

- [ ] **Step 5: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_logo.py -v`
Expected: PASS

- [ ] **Step 6: Коммит**

```bash
git add execution/backend/app/companies/logo.py execution/backend/tests/test_companies_logo.py
git commit -m "fix: логотип в /wp-content/uploads/ больше не отбрасывается"
```

---

### Task 9: Inline `<svg>` в шапке как источник логотипа

**Files:**
- Modify: `execution/backend/app/companies/logo.py`
- Modify: `execution/backend/app/companies/builder.py` (`_find_logo`)
- Test: `execution/backend/tests/test_companies_logo.py`, `tests/test_companies_builder.py`

- [ ] **Step 1: Написать падающие тесты поиска**

```python
def test_find_logo_returns_inline_svg_from_header():
    """rubkoff.ru: логотип — inline <svg> внутри <a class="header__logo">,
    <img> на странице нет. Именно этот случай имелся в виду в жалобе «не все
    svg умеем сохранять»."""
    html = """
    <header class="header">
      <a class="header__logo" href="/">
        <svg width="169" height="18" viewBox="0 0 169 18"><path d="M124 1.5"/></svg>
      </a>
    </header>
    """
    candidate = find_logo(html, "https://rubkoff.ru")
    assert candidate.url == ""
    assert "<svg" in candidate.svg_markup
    assert 'xmlns="http://www.w3.org/2000/svg"' in candidate.svg_markup


def test_find_logo_prefers_img_over_inline_svg():
    """Файл предпочтительнее разметки: его можно перезалить как есть."""
    html = """
    <header>
      <a class="logo"><svg><path d="M0 0"/></svg></a>
      <img class="logo" src="/img/logo.png">
    </header>
    """
    candidate = find_logo(html, "https://dom.ru")
    assert candidate.url == "https://dom.ru/img/logo.png"
    assert candidate.svg_markup == ""


def test_find_logo_ignores_inline_svg_in_partner_block():
    html = """
    <div class="partners"><a class="logo"><svg><path d="M0 0"/></svg></a></div>
    """
    assert not find_logo(html, "https://dom.ru")
```

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_logo.py -v`
Expected: FAIL — `assert '<svg' in ''`

- [ ] **Step 3: Реализовать поиск inline-svg**

```python
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def _with_xmlns(markup: str) -> str:
    """Без xmlns отдельный .svg-файл не откроется ни в браузере, ни в
    редакторе: в HTML пространство имён подразумевается, в самостоятельном
    файле — нет."""
    if "xmlns=" in markup:
        return markup
    return markup.replace("<svg", f'<svg xmlns="{_SVG_NAMESPACE}"', 1)


def _find_svg_in_scope(scope) -> str:
    for container in [t for t in scope.find_all(True) if _is_logo_candidate(t)]:
        svg = container.find("svg")
        if svg is not None and not _in_skipped_container(svg):
            return _with_xmlns(str(svg))
    return ""
```

И в `find_logo` после цикла по контейнерам, до `return LogoCandidate()`:

```python
    svg_markup = _find_svg_in_scope(soup)
    if svg_markup:
        return LogoCandidate(svg_markup=svg_markup)
    return LogoCandidate()
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_logo.py -v`
Expected: PASS

- [ ] **Step 5: Написать падающий тест заливки svg билдером**

В `tests/test_companies_builder.py`:

```python
def test_build_uploads_inline_svg_logo_as_file(db_session, site, company):
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом"))
    db_session.commit()

    site_client = Mock(
        create_page=Mock(return_value={"id": 99, "url": "/s/ooo-dom-samara/"}),
        create_teaser=Mock(return_value=555),
        upload_file=Mock(return_value="/media/uploads/service-img/cp-company-7-logo.svg"))
    logo_fn = Mock(return_value=LogoCandidate(svg_markup='<svg xmlns="x"><path/></svg>'))

    _builder(db_session, company, site, site_client=site_client, logo_fn=logo_fn).build()

    data, filename, _ = site_client.upload_file.call_args.args
    assert filename == "cp-company-7-logo.svg"
    assert data == b'<svg xmlns="x"><path/></svg>'
    assert company.info.builder_logo_src == "/media/uploads/service-img/cp-company-7-logo.svg"
```

- [ ] **Step 6: Запустить тест и убедиться, что падает**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_builder.py::test_build_uploads_inline_svg_logo_as_file -v`
Expected: FAIL — `upload_file` не вызывался

- [ ] **Step 7: Заливать разметку в `_find_logo`**

```python
    def _find_logo(self, info: CompanyInfo) -> None:
        """Логотип из выгрузки Яндекса («Логотип», 78% строк) приезжает в
        builder_logo_src через refresh_info_from_candidate. Скрейпинг шапки
        сайта компании — запасной источник для остальных."""
        if info.builder_logo_src:
            return
        candidate = self.logo_fn(self.company.website)
        if candidate.svg_markup:
            self._upload_logo(info, candidate.svg_markup.encode("utf-8"), ".svg")
        elif candidate.url:
            info.builder_logo_src = candidate.url
            self.db.commit()
```

- [ ] **Step 8: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_logo.py tests/test_companies_builder.py -v`
Expected: PASS

- [ ] **Step 9: Коммит**

```bash
git add execution/backend/app/companies/logo.py execution/backend/app/companies/builder.py execution/backend/tests/test_companies_logo.py execution/backend/tests/test_companies_builder.py
git commit -m "feat: inline-svg логотип из шапки сайта сохраняется как файл"
```

---

### Task 10: Обход cookie-заглушки хостинга

**Files:**
- Modify: `execution/backend/app/companies/logo.py:70-77`
- Test: `execution/backend/tests/test_companies_logo.py`

- [ ] **Step 1: Написать падающий тест**

```python
def test_fetch_company_logo_retries_after_cookie_stub():
    """timesvai.ru отдаёт 274 байта JavaScript, который ставит cookie
    beget=begetok и перезагружает страницу. Без повтора мы разбираем
    заглушку вместо сайта."""
    stub = ("<html><head><script>function set_cookie(){document.cookie="
            "'beget=begetok';}set_cookie();location.reload();</script></head></html>")
    real = '<header><img class="logo" src="/img/logo.png"></header>'
    responses = [Mock(text=stub, status_code=200), Mock(text=real, status_code=200)]
    for r in responses:
        r.raise_for_status = Mock()
    session = Mock(get=Mock(side_effect=responses), cookies=Mock(set=Mock()))
    with patch("app.companies.logo.requests.Session", return_value=session):
        candidate = fetch_company_logo("https://timesvai.ru")

    assert candidate.url == "https://timesvai.ru/img/logo.png"
    session.cookies.set.assert_called_once_with("beget", "begetok")
    assert session.get.call_count == 2
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_logo.py::test_fetch_company_logo_retries_after_cookie_stub -v`
Expected: FAIL — `AttributeError: <module 'app.companies.logo'> does not have the attribute 'requests.Session'` либо `session.get.call_count == 1`

- [ ] **Step 3: Реализовать повтор**

```python
# Короткий ответ, который только ставит cookie и перезагружает страницу —
# защита хостинга (Beget) от ботов. Настоящая страница приходит вторым
# запросом, уже с cookie.
_COOKIE_STUB_LIMIT_BYTES = 1500
_COOKIE_STUB = re.compile(r"set_cookie|document\.cookie", re.I)


def fetch_company_logo(website: str) -> LogoCandidate:
    session = requests.Session()
    try:
        response = session.get(website, headers=_HEADERS, timeout=_TIMEOUT_SECONDS,
                               allow_redirects=True)
        response.raise_for_status()
        if (len(response.text) < _COOKIE_STUB_LIMIT_BYTES
                and _COOKIE_STUB.search(response.text)):
            session.cookies.set("beget", "begetok")
            response = session.get(website, headers=_HEADERS, timeout=_TIMEOUT_SECONDS,
                                   allow_redirects=True)
            response.raise_for_status()
    except requests.RequestException:
        return LogoCandidate()
    return find_logo(response.text, website.rstrip("/"))
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_logo.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add execution/backend/app/companies/logo.py execution/backend/tests/test_companies_logo.py
git commit -m "fix: обход cookie-заглушки хостинга при поиске логотипа"
```

---

### Task 11: Шаблон прячет лишний элемент логотипа вместо удаления

**Files:**
- Modify: `execution/backend/app/companies/template.py:57-70`
- Test: `execution/backend/tests/test_companies_template.py`

- [ ] **Step 1: Написать падающие тесты**

```python
def test_fill_keeps_both_logo_elements_when_logo_present():
    """Ключевая защита от петли: шаблон синхронизируется с уже собранной
    страницы (sync_builder_reference), поэтому удалённый при сборке элемент
    исчезал бы из шаблона навсегда. На проде так потерялась запасная подпись
    у всех 13 сайтов."""
    html = fill_builder_template(TEMPLATE, _info(builder_logo_src="/media/logo.svg"))
    assert 'id="builder-logo"' in html
    assert 'id="builder-logo-text"' in html


def test_fill_shows_logo_and_hides_text_when_logo_present():
    html = fill_builder_template(TEMPLATE, _info(builder_logo_src="/media/logo.svg"))
    soup = BeautifulSoup(html, "html.parser")
    assert soup.find(id="builder-logo").get("src") == "/media/logo.svg"
    assert not soup.find(id="builder-logo").has_attr("hidden")
    assert soup.find(id="builder-logo-text").has_attr("hidden")
    assert soup.find(id="builder-logo-text").get_text(strip=True) == ""


def test_fill_shows_name_and_hides_image_when_logo_missing():
    """Страница 338 на bolars.ru (Рубкофф) осталась без логотипа И без
    названия — картинку удалили, а подписи в шаблоне не было."""
    html = fill_builder_template(TEMPLATE, _info(builder_logo_src=""))
    soup = BeautifulSoup(html, "html.parser")
    assert soup.find(id="builder-logo-text").get_text(strip=True) == "ООО Дом"
    assert not soup.find(id="builder-logo-text").has_attr("hidden")
    logo = soup.find(id="builder-logo")
    assert logo.has_attr("hidden")
    assert not logo.has_attr("src")


def test_fill_hidden_image_keeps_reference_src_in_data_attribute():
    """Скрытая картинка без src не делает запроса, но исходный адрес из
    эталона сохраняется — чтобы разметка дожила до следующей синхронизации
    осмысленной."""
    template = TEMPLATE.replace('<img id="builder-logo" src="" alt="">',
                                '<img id="builder-logo" src="/media/ref.svg" alt="">')
    html = fill_builder_template(template, _info(builder_logo_src=""))
    soup = BeautifulSoup(html, "html.parser")
    assert soup.find(id="builder-logo").get("data-src") == "/media/ref.svg"
```

Добавить в начало файла `from bs4 import BeautifulSoup`.

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_template.py -v`
Expected: FAIL — `AttributeError: 'NoneType' object has no attribute 'get'` (элемент удалён)

- [ ] **Step 3: Заменить удаление на скрытие**

В `app/companies/template.py` добавить после `_set_text`:

```python
def _hide(tag) -> None:
    """Лишний элемент блока логотипа скрывается, а не удаляется: шаблон
    сайта синхронизируется с уже собранной страницы (app/companies/
    reference.py), поэтому удалённый элемент исчез бы из шаблона навсегда —
    и следующая компания осталась бы либо без картинки, либо без подписи."""
    tag["hidden"] = ""
    if tag.name == "img" and tag.get("src"):
        # без src скрытая картинка не делает лишнего запроса
        tag["data-src"] = tag["src"]
        del tag["src"]
```

И заменить блок логотипа:

```python
    logo_img = soup.find(id="builder-logo")
    logo_text_span = soup.find(id="builder-logo-text")
    if logo_src:
        if logo_img:
            logo_img["src"] = logo_src
            logo_img["alt"] = logo_alt
            del logo_img["hidden"]
        if logo_text_span:
            _set_text(logo_text_span, "")
            _hide(logo_text_span)
    else:
        if logo_img:
            _hide(logo_img)
        if logo_text_span:
            _set_text(logo_text_span, name)
            del logo_text_span["hidden"]
```

`del tag["hidden"]` на элементе без атрибута — no-op у BeautifulSoup
(`attrs.pop(key, None)`), отдельная проверка не нужна.

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_template.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add execution/backend/app/companies/template.py execution/backend/tests/test_companies_template.py
git commit -m "fix: блок логотипа прячется вместо удаления — шаблон больше не теряет элементы"
```

---

### Task 12: Контракт эталона требует оба элемента логотипа

**Files:**
- Modify: `execution/backend/app/companies/reference.py:24-35`
- Test: `execution/backend/tests/test_companies_reference.py`

- [ ] **Step 1: Написать падающие тесты**

```python
def test_missing_markers_rejects_reference_without_logo_text():
    from app.companies.reference import _missing_markers

    html = """
    <div id="builder">
      <img id="builder-logo" src="/media/logo.svg">
      <h2 id="builder-main-title"></h2>
      <div id="builder-contacts"><div id="builder-contacts-grid">
        <div id="builder-contact-1"></div></div></div>
    </div>
    """
    assert "builder-logo-text" in _missing_markers(html)


def test_missing_markers_treats_commented_out_element_as_absent():
    """Именно так эталоны и выглядят на проде: запасная подпись лежит внутри
    HTML-комментария, а fill_builder_template вырезает комментарии первым
    делом — значит для заполнения её нет."""
    from app.companies.reference import _missing_markers

    html = """
    <div id="builder">
      <img id="builder-logo" src="/media/logo.svg">
      <!-- <span class="h2 builder-logo-text" id="builder-logo-text">ПИК</span> -->
      <h2 id="builder-main-title"></h2>
      <div id="builder-contacts"><div id="builder-contacts-grid">
        <div id="builder-contact-1"></div></div></div>
    </div>
    """
    assert "builder-logo-text" in _missing_markers(html)


def test_missing_markers_accepts_reference_with_both_logo_elements():
    from app.companies.reference import _missing_markers

    html = """
    <div id="builder">
      <img id="builder-logo" src="/media/logo.svg">
      <span class="h2 builder-logo-text" id="builder-logo-text" hidden></span>
      <h2 id="builder-main-title"></h2>
      <div id="builder-contacts"><div id="builder-contacts-grid">
        <div id="builder-contact-1"></div></div></div>
    </div>
    """
    assert _missing_markers(html) == []
```

Одновременно дополнить `_VALID_TEMPLATE` (`tests/test_companies_reference.py:6-13`)
обоими элементами — иначе `test_sync_caches_template_html` и
`test_sync_failure_does_not_clobber_previous_cache` начнут падать, потому что
их «валидный» эталон перестанет быть валидным:

```python
_VALID_TEMPLATE = (
    '<div id="builder">'
    '<img id="builder-logo" src="/media/logo.svg">'
    '<span class="h2 builder-logo-text" id="builder-logo-text"></span>'
    '<h1 id="builder-main-title"></h1>'
    '<div id="builder-contacts">'
    '<div id="builder-contacts-grid">'
    '<div id="builder-contact-1"></div>'
    '</div></div></div>'
)
```

Три теста с отказами (`test_sync_rejects_page_missing_main_title`,
`..._contacts_grid`, `..._contact_template_item`) править не нужно: они ждут
`ReferenceError` по `match=`-подстроке, которая останется в списке
недостающих маркеров.

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_reference.py -v`
Expected: FAIL — `assert 'builder-logo-text' in []`

- [ ] **Step 3: Расширить контракт**

```python
from bs4 import BeautifulSoup, Comment

# Элементы, без которых fill_builder_template не соберёт карточку. Блок
# логотипа входит сюда с сентября 2026: без builder-logo-text компания без
# логотипа остаётся и без картинки, и без названия (страница 338 на
# bolars.ru), а без builder-logo картинку негде показать.
_REQUIRED_MARKERS = ("builder-logo", "builder-logo-text", "builder-main-title",
                     "builder-contacts", "builder-contacts-grid")


def _missing_markers(html: str) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    # Комментарии вырезаются до поиска — ровно как это делает
    # fill_builder_template: закомментированный элемент для заполнения не
    # существует, и считать его присутствующим значит принять шаблон,
    # который молча потеряет подпись.
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()
    missing = [marker for marker in _REQUIRED_MARKERS if soup.find(id=marker) is None]
    grid = soup.find(id="builder-contacts-grid")
    if grid is not None and grid.find(id="builder-contact-1") is None:
        missing.append("builder-contact-1")
    return missing
```

- [ ] **Step 4: Уточнить текст ошибки синхронизации**

В `sync_builder_reference`:

```python
    if missing:
        raise ReferenceError(
            "в эталонной странице нет обязательных элементов шаблона: "
            f"{', '.join(missing)} — если элемент есть, но закомментирован, "
            "комментарий нужно снять: сервис вырезает комментарии перед "
            "заполнением")
```

- [ ] **Step 5: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_reference.py -v`
Expected: PASS

- [ ] **Step 6: Коммит**

```bash
git add execution/backend/app/companies/reference.py execution/backend/tests/test_companies_reference.py
git commit -m "fix: контракт эталона требует оба элемента логотипа и не считает комментарий разметкой"
```

---

### Task 13: График работы в тизере, пустой адрес — внятный отказ

**Files:**
- Modify: `execution/backend/app/sites/client.py:262-308` (`create_teaser`, `update_teaser` — оба уходят через ретрай-обёртку `self._send`, но тесты по-прежнему патчат `app.sites.client.requests.post`/`.patch`, так как функция передаётся в `_send` аргументом и резолвится из модуля в момент вызова)
- Modify: `execution/backend/app/companies/builder.py` (`_create_teaser`)
- Test: `execution/backend/tests/test_sites_client.py`, `tests/test_companies_builder.py`

- [ ] **Step 1: Написать падающий тест клиента**

```python
def test_create_teaser_sends_working_hours_as_description():
    """description у тизера — это режим работы: у 116 из 117 заведённых
    руками карточек он заполнен, у всех созданных сервисом был пуст."""
    client = SiteClient("https://s.ru", "tok")
    response = Mock(ok=True, status_code=201)
    response.json.return_value = {"id": 42}
    with patch("app.sites.client.requests.post", return_value=response) as post:
        client.create_teaser(
            name="ООО Дом", slug="ooo-dom-samara", address="ул. Ленина 1",
            phone="79991234567", email="info@dom.ru", website="https://dom.ru",
            page_url="/s/ooo-dom-samara/", category=3, city=1, location=1,
            description="пн-пт 09:00–18:00")
    assert post.call_args.kwargs["json"]["description"] == "пн-пт 09:00–18:00"


def test_update_teaser_sends_working_hours_as_description():
    client = SiteClient("https://s.ru", "tok")
    response = Mock(ok=True, status_code=200)
    response.json.return_value = {"id": 42}
    with patch("app.sites.client.requests.patch", return_value=response) as patch_req:
        client.update_teaser(
            42, name="ООО Дом", slug="ooo-dom-samara", address="ул. Ленина 1",
            phone="79991234567", email="info@dom.ru", website="https://dom.ru",
            page_url="/s/ooo-dom-samara/", category=3, city=1, location=1,
            description="пн-пт 09:00–18:00")
    assert patch_req.call_args.kwargs["json"]["description"] == "пн-пт 09:00–18:00"
```

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_sites_client.py -v`
Expected: FAIL — `TypeError: create_teaser() got an unexpected keyword argument 'description'`

- [ ] **Step 3: Добавить параметр в клиент**

В `create_teaser` и `update_teaser` добавить в сигнатуру `description: str = ""`
(рядом с `coordinates: str = ""`) и в оба `payload`:

```python
            "is_active": False, "location": location, "category": category, "city": city,
            "description": description,
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_sites_client.py -v`
Expected: PASS

- [ ] **Step 5: Написать падающие тесты билдера**

```python
def test_teaser_gets_working_hours_from_contacts(db_session, site, company):
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(
        company_id=company.id, builder_name="ООО Дом", address="ул. Ленина 1",
        contacts=[{"address": "ул. Ленина 1", "phone_tel": "+7 846 000-00-00",
                  "working_hours": "пн-пт 09:00–18:00"}]))
    db_session.commit()

    site_client = Mock(create_page=Mock(return_value={"id": 99, "url": "/s/x/"}),
                       create_teaser=Mock(return_value=555), upload_file=Mock())
    _builder(db_session, company, site, site_client=site_client).build()

    assert site_client.create_teaser.call_args.kwargs["description"] == "пн-пт 09:00–18:00"


def test_build_fails_readably_when_address_is_empty(db_session, site, company):
    """Партия 11 (Тверь) легла целиком с «создание тизера: HTTP 400:
    {"address":["Это поле не может быть пустым."]}» — сообщение ничего не
    говорит оператору о том, что делать."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               address="", contacts=[]))
    db_session.commit()

    site_client = Mock(create_page=Mock(return_value={"id": 99, "url": "/s/x/"}),
                       create_teaser=Mock(), upload_file=Mock())
    _builder(db_session, company, site, site_client=site_client).build()

    site_client.create_teaser.assert_not_called()
    assert company.status == "failed"
    assert "адрес" in company.error_text.lower()
```

- [ ] **Step 6: Запустить тесты и убедиться, что падают**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_builder.py -v`
Expected: FAIL — `KeyError: 'description'`

- [ ] **Step 7: Передать график и проверить адрес**

В `_create_teaser` заменить начало:

```python
    def _create_teaser(self, info: CompanyInfo, page: dict, batch: CompanyBatch) -> None:
        contacts = info.contacts or [{}]
        contact = contacts[0]
        address = contact.get("address", "") or info.address
        # Сайт отвергает тизер без адреса (HTTP 400), но своим текстом не
        # объясняет оператору, что делать — проверяем до запроса.
        if not address:
            raise SiteAPIError(
                "у компании нет адреса — тизер без адреса сайт не принимает; "
                "заполните адрес у кандидата или исключите компанию из партии")
        kwargs = dict(
            name=info.builder_name or self.company.name,
            slug=page.get("url", "").removeprefix("/s/").rstrip("/"),
            address=address,
            phone=normalize_phone(contact.get("phone_tel", "")), email=contact.get("email", ""),
            website=self.company.website, page_url=page.get("url", ""),
            category=batch.teaser_category_id, city=batch.teaser_city_id,
            location=batch.teaser_location_id, coordinates=info.coordinates or "",
            description=contact.get("working_hours", "") or "",
        )
```

- [ ] **Step 8: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_builder.py tests/test_sites_client.py -v`
Expected: PASS

- [ ] **Step 9: Коммит**

```bash
git add execution/backend/app/sites/client.py execution/backend/app/companies/builder.py execution/backend/tests/test_sites_client.py execution/backend/tests/test_companies_builder.py
git commit -m "feat: график работы в тизере, внятный отказ при пустом адресе"
```

---

### Task 14: Предложный падеж города

**Files:**
- Modify: `execution/backend/app/seed.py:137-165`
- Modify: `execution/backend/app/companies/builder.py` (`_apply_ai_fields`, `_create_page`)
- Test: `execution/backend/tests/test_companies_builder.py`

- [ ] **Step 1: Написать падающие тесты**

```python
def test_city_prepositional_is_taken_from_model_when_returned(db_session, site, company):
    """«Рубкофф — монтаж в Москва» в заголовке и «Картек-Строй в рабочий
    посёлок Нахабино» в карточке — city_prepositional не заполнялся нигде."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               city_name="Самара", address="ул. Ленина 1",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    text_client = Mock(complete_json=Mock(return_value=JsonResult(
        data={"about_company": "Строим дома.", "specialization": "Каркас.",
              "projects_services": "50 проектов.", "benefits": "Гарантия.",
              "city_prepositional": "Самаре"},
        tokens_prompt=10, tokens_completion=20, cost=0.01)))
    site_client = Mock(create_page=Mock(return_value={"id": 99, "url": "/s/x/"}),
                       create_teaser=Mock(return_value=555), upload_file=Mock())

    _builder(db_session, company, site, text_client=text_client,
             site_client=site_client).build()

    assert company.info.city_prepositional == "Самаре"
    assert "в Самаре" in site_client.create_page.call_args.kwargs["title"]


def test_build_survives_response_without_city_prepositional(db_session, site, company):
    """Промпт в БД у прода старый (seed_prompts его не перезаписывает), поэтому
    поле обязано остаться необязательным."""
    _seed_prompts(db_session)
    db_session.add(site)
    db_session.add(company.batch)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               city_name="Самара", address="ул. Ленина 1",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    site_client = Mock(create_page=Mock(return_value={"id": 99, "url": "/s/x/"}),
                       create_teaser=Mock(return_value=555), upload_file=Mock())
    _builder(db_session, company, site, site_client=site_client).build()

    assert company.status == "published"
    assert "в Самара" in site_client.create_page.call_args.kwargs["title"]


def test_slug_stays_nominative_regardless_of_prepositional(db_session, site, company):
    """Slug детерминирован и уже опубликован — менять его нельзя, иначе
    пересборка создаст дубль страницы вместо обновления."""
    assert slug_for_company("ООО Дом", "Самара") == "ooo-dom-samara"
```

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_builder.py -v`
Expected: FAIL — `assert '' == 'Самаре'`

- [ ] **Step 3: Принимать необязательное поле от модели**

В `_apply_ai_fields`:

```python
    def _apply_ai_fields(self, info: CompanyInfo, ai_fields: dict, scraped_text: str) -> None:
        # YANDEX_INFO_FIELDS не трогаются — только четыре текстовых поля.
        for field in AI_TEXT_FIELDS:
            setattr(info, field, ai_fields[field])
        # Пятое поле необязательное: промпт в БД мог остаться старым
        # (seed_prompts не перезаписывает уже сохранённый), и тогда работает
        # прежний запас — city_prepositional or city_name в шаблоне.
        prepositional = str(ai_fields.get("city_prepositional") or "").strip()
        if prepositional:
            info.city_prepositional = prepositional
        info.scraped_text = scraped_text
        self.db.commit()
```

- [ ] **Step 4: Использовать падеж в заголовке и описании страницы**

В `_create_page`, в ветке создания новой страницы:

```python
            name = info.builder_name or self.company.name
            city = info.city_name or self.company.region
            # Slug остаётся в именительном падеже: он детерминирован и уже
            # опубликован, смена ломала бы обновление существующих страниц.
            slug = slug_for_company(name, city)
            city_in = info.city_prepositional or city
            page = self.site_client.create_page(
                title=f"{name} — {self.company.category_normalized} в {city_in}",
                url=f"/s/{slug}/", html=html, parent_id=self.site.builder_parent_id,
                meta_description=f"{name} — {self.company.category_normalized} в {city_in}. "
                                 f"Контакты, услуги, отзывы.",
            )
```

- [ ] **Step 5: Обновить дефолтный промпт**

В `app/seed.py`, в конце `DEFAULT_PROMPTS["builder_text"]`, заменить последний абзац:

```
Верни СТРОГО JSON-объект с пятью полями:
{"about_company": "...", "specialization": "...", "projects_services": "...",
 "benefits": "...", "city_prepositional": "..."}

city_prepositional — название города «{{ city }}» в предложном падеже, как
оно встанет во фразу «компания работает в ...»: Москва → Москве, Тверь →
Твери, Ярославль → Ярославле, «рабочий посёлок Нахабино» → «рабочем посёлке
Нахабино». Только форма названия, без предлога.
```

- [ ] **Step 6: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_builder.py tests/test_ai_prompts.py -v`
Expected: PASS

- [ ] **Step 7: Коммит**

```bash
git add execution/backend/app/seed.py execution/backend/app/companies/builder.py execution/backend/tests/test_companies_builder.py
git commit -m "feat: город в предложном падеже в заголовке и карточке строителя"
```

---

### Task 15: Двойной слэш в `remote_url`

**Files:**
- Modify: `execution/backend/app/companies/builder.py` (`_create_page`)
- Test: `execution/backend/tests/test_companies_builder.py`

- [ ] **Step 1: Написать падающий тест**

```python
def test_remote_url_has_no_double_slash_for_base_url_with_trailing_slash(db_session, company):
    """На проде: https://stroybaza-tveri.ru//s/tverstroy-tver/ — у сайта
    base_url оканчивается слэшем."""
    _seed_prompts(db_session)
    site = Site(id=1, name="С", domain="s.ru", base_url="https://s.ru/",
               api_token_enc="e",
               builder_template_html='<div id="builder"><h1 id="builder-main-title">'
                                     '</h1></div>',
               builder_parent_id=10, tone_of_voice="деловой")
    db_session.add(site)
    db_session.add(company.batch)
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id, builder_name="ООО Дом",
                               address="ул. Ленина 1",
                               contacts=[{"address": "ул. Ленина 1"}]))
    db_session.commit()

    _builder(db_session, company, site).build()

    assert company.remote_url == "https://s.ru/s/ooo-dom-samara/"
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_builder.py::test_remote_url_has_no_double_slash_for_base_url_with_trailing_slash -v`
Expected: FAIL — `assert 'https://s.ru//s/ooo-dom-samara/' == 'https://s.ru/s/ooo-dom-samara/'`

- [ ] **Step 3: Срезать слэш базы в обеих ветках `_create_page`**

Заменить оба места, где собирается `remote_url`:

```python
        base_url = self.site.base_url.rstrip("/")
```

и далее `f"{base_url}{page['url']}"` / `f"{base_url}{page.get('url', '')}"`.

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_companies_builder.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add execution/backend/app/companies/builder.py execution/backend/tests/test_companies_builder.py
git commit -m "fix: двойной слэш в remote_url у сайтов с base_url на слэше"
```

---

### Task 16: Разовый скрипт правки эталонных страниц

**Files:**
- Create: `execution/backend/fix_builder_reference_logo_text.py`
- Test: `execution/backend/tests/test_fix_builder_reference.py`

Контракт эталона из Task 12 уронит синхронизацию на всех 13 сайтах, пока
живого `builder-logo-text` там нет. Скрипт снимает комментарий или вставляет
элемент рядом с `builder-logo`.

- [ ] **Step 1: Написать падающие тесты**

```python
from fix_builder_reference_logo_text import ensure_logo_text


def test_uncomments_existing_logo_text_span():
    """9 сайтов из 13: span есть, но закомментирован."""
    html = """
    <div id="builder">
      <img id="builder-logo" src="/media/logo-pik.svg" alt="ПИК">
      <!--
        <span class="h2 builder-logo-text" id="builder-logo-text">ПИК</span>
      -->
    </div>
    """
    fixed, changed = ensure_logo_text(html)
    assert changed is True
    assert '<!--' not in fixed
    assert 'id="builder-logo-text"' in fixed


def test_inserts_logo_text_span_when_absent():
    """4 сайта из 13: элемента нет вовсе."""
    html = '<div id="builder"><img id="builder-logo" src="/media/logo.svg"></div>'
    fixed, changed = ensure_logo_text(html)
    assert changed is True
    assert 'class="h2 builder-logo-text"' in fixed
    assert 'id="builder-logo-text"' in fixed


def test_leaves_page_untouched_when_span_already_live():
    html = ('<div id="builder"><img id="builder-logo" src="/media/logo.svg">'
            '<span class="h2 builder-logo-text" id="builder-logo-text"></span></div>')
    fixed, changed = ensure_logo_text(html)
    assert changed is False
    assert fixed == html


def test_reports_page_without_logo_image():
    """Без builder-logo вставлять подпись некуда — такую страницу трогать
    нельзя, её должен посмотреть человек."""
    html = '<div id="builder"><h2 id="builder-main-title"></h2></div>'
    fixed, changed = ensure_logo_text(html)
    assert changed is False
    assert fixed == html
```

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_fix_builder_reference.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fix_builder_reference_logo_text'`

- [ ] **Step 3: Написать скрипт**

```python
"""Разовая правка эталонных страниц строителя: запасная подпись
(builder-logo-text) должна быть живой разметкой, а не комментарием.

На проде 10.09.2026 живого элемента не было ни у одного из 13 сайтов: у 9 он
лежал внутри HTML-комментария, у 4 отсутствовал. fill_builder_template
вырезает комментарии перед заполнением, поэтому компания без логотипа
оставалась и без картинки, и без названия.

Запуск (из /app в контейнере api):
    python fix_builder_reference_logo_text.py            # показать, что будет сделано
    python fix_builder_reference_logo_text.py --apply    # записать на сайты
"""

from __future__ import annotations

import argparse
import re

from bs4 import BeautifulSoup, Comment

from app.api.admin_sites import open_client
from app.db import SessionLocal
from app.models.site import Site

_SPAN_MARKUP = '<span class="h2 builder-logo-text" id="builder-logo-text"></span>'
_COMMENTED_SPAN = re.compile(r'<span[^>]*id="builder-logo-text".*?</span>', re.S)


def ensure_logo_text(html: str) -> tuple[str, bool]:
    """Возвращает (html, был_ли_изменён). Страницу без builder-logo не трогает."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.find(id="builder-logo-text") is not None:
        return html, False
    logo_img = soup.find(id="builder-logo")
    if logo_img is None:
        return html, False

    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        match = _COMMENTED_SPAN.search(str(comment))
        if match:
            comment.replace_with(BeautifulSoup(match.group(0), "html.parser"))
            return str(soup), True

    logo_img.insert_after(BeautifulSoup(_SPAN_MARKUP, "html.parser"))
    return str(soup), True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="записать изменения на сайты (без флага — только показать)")
    args = parser.parse_args()

    db = SessionLocal()
    for site in db.query(Site).filter(Site.builder_reference_id.isnot(None)).all():
        client = open_client(db, site)
        page = client.get_page(site.builder_reference_id)
        html = page.get("text") or page.get("body") or ""
        fixed, changed = ensure_logo_text(html)
        if not changed:
            print(f"{site.name}: правка не требуется или нет builder-logo — пропускаю")
            continue
        if args.apply:
            client.update_page_text(site.builder_reference_id, fixed)
            print(f"{site.name}: эталон {site.builder_reference_id} обновлён")
        else:
            print(f"{site.name}: эталон {site.builder_reference_id} будет обновлён")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_fix_builder_reference.py -v`
Expected: PASS, 4 теста

- [ ] **Step 5: Коммит**

```bash
git add execution/backend/fix_builder_reference_logo_text.py execution/backend/tests/test_fix_builder_reference.py
git commit -m "feat: разовый скрипт правки запасной подписи в эталонах строителя"
```

---

### Task 17: Разовый скрипт обнуления логотипов

**Files:**
- Create: `execution/backend/reset_builder_logos.py`
- Test: `execution/backend/tests/test_reset_builder_logos.py`

Без обнуления пересборка не тронет ни 9 битых SVG, ни 2 `data:`-заглушки:
`_relocate_logo` пропускает всё, что начинается с `/`.

- [ ] **Step 1: Написать падающий тест**

```python
from reset_builder_logos import reset_logos
from app.models.company import Company, CompanyInfo


def test_reset_clears_logos_of_batch_companies(db_session):
    company = Company(site_id=1, batch_id=1, candidate_id=1, site_key="dom.ru",
                     name="ООО Дом")
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id,
                               builder_logo_src="/media/uploads/service-img/x.webp"))
    db_session.commit()

    assert reset_logos(db_session) == 1
    assert company.info.builder_logo_src == ""


def test_reset_skips_companies_migrated_from_cli(db_session):
    """У мигрированных из CLI компаний кандидата нет: обнулив логотип, мы
    оставили бы их карточки вообще без картинки — пересобрать их нечем."""
    company = Company(site_id=1, batch_id=None, candidate_id=None, site_key="old.ru",
                     name="Старая")
    db_session.add(company)
    db_session.flush()
    db_session.add(CompanyInfo(company_id=company.id,
                               builder_logo_src="https://old.ru/logo.png"))
    db_session.commit()

    assert reset_logos(db_session) == 0
    assert company.info.builder_logo_src == "https://old.ru/logo.png"
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_reset_builder_logos.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reset_builder_logos'`

- [ ] **Step 3: Написать скрипт**

```python
"""Разовое обнуление builder_logo_src у компаний из партий перед массовой
пересборкой.

_relocate_logo пропускает всё, что начинается с '/', поэтому уже залитые
логотипы (в том числе 9 SVG под именем .webp, которые браузер не рисует) при
пересборке остались бы как есть. После обнуления цепочка «выгрузка →
скрейпинг» разрешает логотип с нуля и заливает с верным расширением.

Мигрированные из CLI компании (candidate_id IS NULL) не трогаются: кандидата
у них нет, заново логотип взять неоткуда.

Запуск (из /app в контейнере api):
    python reset_builder_logos.py
"""

from __future__ import annotations

from app.db import SessionLocal
from app.models.company import Company


def reset_logos(db) -> int:
    companies = db.query(Company).filter(Company.candidate_id.isnot(None)).all()
    reset = 0
    for company in companies:
        if company.info is None or not company.info.builder_logo_src:
            continue
        company.info.builder_logo_src = ""
        reset += 1
    db.commit()
    return reset


def main() -> None:
    db = SessionLocal()
    print(f"обнулено логотипов: {reset_logos(db)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q tests/test_reset_builder_logos.py -v`
Expected: PASS

- [ ] **Step 5: Полный регресс бэкенда**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/.claude/worktrees/builders-data-quality/execution && docker compose run --rm --no-deps backend pytest -q`
Expected: PASS, падений нет

- [ ] **Step 6: Коммит**

```bash
git add execution/backend/reset_builder_logos.py execution/backend/tests/test_reset_builder_logos.py
git commit -m "feat: разовый скрипт обнуления логотипов перед пересборкой"
```

---

### Task 18: Выкатка и разовые работы на проде

Кода не содержит — порядок операций, каждая опирается на предыдущую.
Выполнять **после** мержа и деплоя, сверяясь с `DEPLOY.md`.

- [ ] **Step 1: Бэкап таблиц строителей**

```bash
ssh k1-panel-vps 'cd /home/panel/k1-content-panel/execution && docker compose exec -T postgres pg_dump -U app -d content -t company_candidates -t company_imports -t companies -t company_info -t company_batches > /home/panel/backups/builders_before_quality_fix_$(date +%Y%m%d_%H%M).sql'
```

- [ ] **Step 2: Деплой и миграция**

```bash
ssh k1-panel-vps 'cd /home/panel/k1-content-panel && git pull && cd execution && docker compose -f docker-compose.prod.yml up -d --build'
curl -s -o /dev/null -w "%{http_code}\n" https://content-panel.nastroyker.ru/api/health
```

Expected: `200`. Если `502` — перезагрузить nginx фронтенда, он держит старый
IP пересозданного контейнера api:

```bash
ssh k1-panel-vps "docker exec execution-frontend-1 nginx -s reload"
```

- [ ] **Step 3: Повторно загрузить июльскую выгрузку**

Через интерфейс панели → «Строители» → загрузка xlsx, файл
`builders_yandex_2026-07-10.xlsx`. Upsert по `site_key` обновит все 5214
кандидатов, новых строк не появится.

Проверка:

```bash
ssh k1-panel-vps 'cd /home/panel/k1-content-panel/execution && docker compose exec -T postgres psql -U app -d content -c "select count(*) total, count(*) filter (where working_hours <> '"''"') with_hours, count(*) filter (where logo_url <> '"''"') with_logo from company_candidates;"'
```

Expected: `total` = 5214, `with_hours` ≈ 4900+, `with_logo` ≈ 4000+

- [ ] **Step 4: Починить эталонные страницы**

```bash
ssh k1-panel-vps 'cd /home/panel/k1-content-panel/execution && docker compose exec -T api python fix_builder_reference_logo_text.py'
```

Посмотреть вывод, затем применить:

```bash
ssh k1-panel-vps 'cd /home/panel/k1-content-panel/execution && docker compose exec -T api python fix_builder_reference_logo_text.py --apply'
```

- [ ] **Step 5: Пересинхронизировать шаблоны**

В панели: «Сайты» → у каждого сайта с эталоном строителя нажать «Проверить и
синхронизировать». Ошибка «нет обязательных элементов шаблона» означает, что
эталон шага 4 не покрыл — такую страницу смотреть руками.

- [ ] **Step 6: Обновить промпт `builder_text`**

В панели: «Промпты» → `builder_text` → дописать пятое поле ответа из Task 14,
Step 5. Без этого город останется в именительном падеже (сборка не упадёт).

- [ ] **Step 7: Обнулить логотипы**

```bash
ssh k1-panel-vps 'cd /home/panel/k1-content-panel/execution && docker compose exec -T api python reset_builder_logos.py'
```

Expected: `обнулено логотипов: 30` (порядок величины; точное число зависит от
состояния на момент запуска)

- [ ] **Step 8: Пересобрать компании партиями**

В панели на странице каждой партии — кнопка «Пересобрать» у компании.
Запускать по 5-10 за раз: у воркера `concurrency=2`, и каждая компания — это
платный вызов RouterAI.

- [ ] **Step 9: Проверить результат на выборке**

```bash
ssh k1-panel-vps 'cd /home/panel/k1-content-panel/execution && docker compose exec -T postgres psql -U app -d content -c "select count(*) total, count(*) filter (where i.builder_logo_src like '"'"'/media/%'"'"') relocated, count(*) filter (where i.builder_logo_src = '"''"') no_logo from companies c join company_info i on i.company_id=c.id where c.batch_id is not null and c.status='"'"'published'"'"';"'
```

Expected: `no_logo` заметно меньше прежних 18 из 64; ни одного значения,
начинающегося с `http` или `data:`.

Затем открыть одну карточку без логотипа и убедиться, что вместо картинки
стоит название компании, а не пустой блок.

---

## Самопроверка плана

**Покрытие спеки:** §1 → Task 1-2; §2 → Task 3-4; §3 → Task 5; §4 → Task 6-10;
§5 → Task 11-12; §6 → Task 13-15; §7 → Task 16-18.

**Сознательно вне плана** (зафиксировано в «Не входит в объём» спеки):
разбор `0509_Яндекс.xlsx`, отчёт о неопознанных колонках, колонка «Фото»,
добор телефона/почты со страницы компании, конвертация растра в WebP, чистка
осиротевших `cp-company-N-logo.webp`, разбор CSS ради `background-image`.

**Измеренное ограничение, принятое сознательно** (найдено ревью Task 3):
при пустом `phone_tel` шаблон (`app/companies/template.py`,
`line("builder-line-phone", bool(phone_tel), ...)`) выбрасывает строку
телефона целиком — вместе с подписью, которая раньше хотя бы показывалась
текстом. По выгрузке таких значений 2 из 5407: `+7 (351) 225-49-08
8-919-400-18-98` (два номера слитно, без разделителя) и `+90 539 574 18 23`
(турецкий номер). Остальные поля контакта при этом рисуются штатно. Править
шаблон ради 0.04% несоразмерно; если на следующей выгрузке доля вырастет —
показывать подпись текстом, без ссылки.
