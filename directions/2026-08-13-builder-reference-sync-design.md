# Эталонная страница строителя вместо ручного HTML-шаблона

## Проблема

`Site.builder_template_html` — обязательное поле для сборки карточки
компании-строителя (`app/companies/builder.py:74-77`, `_require_template`),
но в форме настроек сайта его никогда не было — поле можно было заполнить
только напрямую в БД. На всех 6 сайтов прода оно пустое (`length = 0`),
поэтому любая попытка сгенерировать компанию падает с ошибкой «у сайта не
задан шаблон карточки строителя».

Решение по аналогии со статьями (`app/sites/reference.py`,
`reference_article_id` → синхронизация → `reference_html`): вместо ручной
вставки HTML в форму — id эталонной страницы **на самом сайте**, откуда
шаблон тянется синхронизацией и кешируется.

**Важное отличие от статей**: `reference_html` статей — произвольный
HTML, стилевой образец для RouterAI (LLM генерирует новую разметку по
мотивам). `builder_template_html` — жёсткий разметочный контракт:
`fill_builder_template()` (`app/companies/template.py`) ищет в шаблоне
строго определённые `id`/`class` (`builder-logo`, `builder-main-title`,
`builder-contacts`, `builder-contacts-grid`, `builder-contact-N`,
`builder-line-*`) и подставляет в них текст детерминированно, без LLM.
Эталонная страница обязана быть уже собрана этим же движком (или вручную
по тому же контракту) — иначе синхронизация молча сохранит шаблон, который
не сможет ничего заполнить.

## Модель данных (`app/models/site.py`)

```python
# --- строители (план 2) ---
builder_template_html: Mapped[str] = mapped_column(Text, default="")   # уже есть, остаётся
builder_parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)   # уже есть
builder_reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)          # новое
builder_reference_synced_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True), nullable=True)                                                # новое
```

`builder_template_html` перестаёт быть полем формы — как `reference_html`
у статей, он управляется только синхронизацией. Убирается из `SiteIn` /
`SiteOut` (`app/api/admin_sites.py`) — остаётся внутренним полем модели.

Миграция Alembic: `add_column(sites, builder_reference_id)`,
`add_column(sites, builder_reference_synced_at)`.

## Синхронизация (`app/companies/reference.py`, новый модуль)

По образцу `app/sites/reference.py::sync_site_reference`:

Исключение — существующий `app.sites.reference.ReferenceError` (импортируется,
не дублируется: `/sync` уже ловит его один раз для обоих шагов).

`builder_parent_id` в этой функции не проверяется — он нужен только при
создании страниц компаний (`builder.py:151`), к синхронизации самого
шаблона отношения не имеет; проверять его здесь означало бы блокировать
синхронизацию шаблона несвязанной настройкой.

```python
from app.sites.reference import ReferenceError

_REQUIRED_MARKERS = ("builder-main-title", "builder-contacts", "builder-contacts-grid")

def _missing_markers(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    missing = [m for m in _REQUIRED_MARKERS if not soup.find(id=m)]
    contacts_grid = soup.find(id="builder-contacts-grid")
    if contacts_grid is not None and not contacts_grid.find(id="builder-contact-1"):
        missing.append("builder-contact-1")
    return missing

def sync_builder_reference(db, site, client, commit=True) -> None:
    if not site.builder_reference_id:
        raise ReferenceError("Эталонная карточка строителя не задана")

    reference = client.get_page(site.builder_reference_id)
    html = reference.get("text") or reference.get("body") or ""
    missing = _missing_markers(html)
    if missing:
        raise ReferenceError(
            "в эталонной странице нет обязательных элементов шаблона: "
            f"{', '.join(missing)} — это точно карточка компании, собранная "
            "этим сервисом?")

    site.builder_template_html = html
    site.builder_reference_synced_at = utcnow()
    if commit:
        db.commit()
```

Проверяются только структурно обязательные маркеры — блоки лого/о
компании/специализации/преимуществ в `fill_builder_template` уже штатно
необязательны (`if not block: return`), поэтому их отсутствие в эталоне не
повод для отказа.

## `POST /admin/sites/{id}/sync` — два независимых шага

Сейчас шаг статей обязателен безусловно (упадёт для сайта без
`articles_parent_id`, даже если сайту нужны только строители). Меняется на:
каждый шаг выполняется только если у сайта заданы его id; отсутствие
конфигурации — не ошибка, а пропуск. Общий `ok` — true, если ни один
**сконфигурированный** шаг не упал.

```python
class SyncResult(BaseModel):
    articles_ok: bool | None = None   # None = пропущено (не сконфигурировано)
    articles_detail: str = ""
    url_prefix: str = ""
    pages: int = 0
    reference_images: int = 0

    builder_ok: bool | None = None
    builder_detail: str = ""

    ok: bool = True   # False, если хоть один сконфигурированный шаг упал
```

Оба шага — в одной транзакции (как сейчас), коммит один раз в конце, если
хотя бы один шаг реально выполнялся (не оба пропущены).

## Фронтенд

- `AdminSitesPage.tsx`: новое поле `builder_reference_id` («ID эталонной
  карточки строителя») рядом с `builder_parent_id`, `extra` показывает
  `builder_reference_synced_at` — по образцу `reference_article_id`
  (`AdminSitesPage.tsx:219-228`).
- `sync()` (`AdminSitesPage.tsx:62-80`): сообщение собирается из двух
  частей («статьи: …» / «строители: …»), каждая по своему результату;
  тост — ошибка, если хоть один сконфигурированный шаг упал.
- `api.ts`: `SiteFull` — заменить `builder_template_html` на
  `builder_reference_id: number | null`, `builder_reference_synced_at:
  string | null`; `SyncResult` — новые поля.
- Список сайтов (таблица в `AdminSitesPage.tsx`) — колонка статуса
  синхронизации строителей, аналогично колонке «Эталон» у статей.

## Тесты

- `app/companies/reference.py`: нет `builder_reference_id` → `ReferenceError`;
  HTML без обязательных id → `ReferenceError` с текстом претензии;
  валидный HTML → `builder_template_html` и `builder_reference_synced_at`
  проставлены.
- `test_api_sites.py` (`/sync`): ничего не сконфигурировано → `ok=true`,
  оба шага `None`; только статьи сконфигурированы и падают → `articles_ok=
  false`, `builder_ok=None`, общий `ok=false`; оба сконфигурированы, один
  падает — раздельные поля, общий `ok=false`.
- `test_companies_builder.py`: билдер компании больше не падает на
  «шаблон не задан», если `builder_template_html` заполнен синхронизацией
  (существующий тест `test_build_fails_when_template_missing` остаётся —
  проверяет случай, когда синхронизация не проводилась).

## Не входит в объём

- Проверка эталона строителей не переиспользует `_missing_markers` для
  необязательных блоков — расширять список проверяемых id по мере
  необходимости, не сейчас.
- Массовая ресинхронизация всех сайтов одной командой — вне объёма,
  ресинхронизация делается по кнопке на карточке сайта, как сейчас со
  статьями.
