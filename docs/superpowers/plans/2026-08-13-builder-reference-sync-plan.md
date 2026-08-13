# Эталонная страница строителя вместо ручного HTML-шаблона — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить ручное поле `builder_template_html` (никогда не имевшее формы в UI, отчего генерация карточек строителя падает с «шаблон не задан») на синхронизацию из эталонной страницы на самом сайте — по образцу того, как уже работает `reference_article_id` у статей.

**Architecture:** Новый модуль `app/companies/reference.py::sync_builder_reference` тянет HTML эталонной страницы через `SiteClient.get_page()`, проверяет обязательные `id`-маркеры разметочного контракта (`fill_builder_template` их ищет детерминированно, не LLM) и кеширует в `Site.builder_template_html`. `POST /admin/sites/{id}/sync` теперь гоняет два независимых шага (эталон статьи / эталон строителя) — отсутствие настройки одного не ломает другой. Фронтенд получает поле `builder_reference_id` в настройках сайта вместо несуществовавшего поля для HTML.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, BeautifulSoup4, pytest · React, antd, TypeScript

**Spec:** `directions/2026-08-13-builder-reference-sync-design.md`

---

## Файлы

- Modify: `execution/backend/app/models/site.py` — новые поля `builder_reference_id`, `builder_reference_synced_at`.
- Create: `execution/backend/alembic/versions/e19ffe68c564_add_builder_reference_fields.py`
- Create: `execution/backend/app/companies/reference.py` — `sync_builder_reference`, проверка id-контракта.
- Create: `execution/backend/tests/test_companies_reference.py`
- Modify: `execution/backend/app/companies/builder.py` — текст ошибки `_require_template`.
- Modify: `execution/backend/app/api/admin_sites.py` — `SiteIn`/`SiteOut`/`_to_out`, `SyncResult`, `sync_site` (два независимых шага).
- Modify: `execution/backend/tests/test_api_sites.py` — переименование `detail`→`articles_detail`, новые тесты шага строителей.
- Modify: `execution/backend/tests/test_models_site.py` — тест новых полей.
- Modify: `execution/frontend/src/api.ts` — `SiteFull`, новый `SyncResult`.
- Modify: `execution/frontend/src/pages/AdminSitesPage.tsx` — поле формы, колонка таблицы, сообщение `sync()`.

---

### Task 1: Модель `Site` — новые поля

**Files:**
- Modify: `execution/backend/app/models/site.py:62-64`

- [ ] **Step 1: Добавить поля в модель**

Заменить конец файла (строки 62-64):

```python
    # --- строители (план 2) ---
    builder_template_html: Mapped[str] = mapped_column(Text, default="")
    builder_parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

на:

```python
    # --- строители (план 2) ---
    builder_template_html: Mapped[str] = mapped_column(Text, default="")
    builder_parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Эталонная карточка строителя на самом сайте — источник builder_template_html,
    # см. app/companies/reference.py::sync_builder_reference. Тот же приём, что
    # reference_article_id/reference_synced_at у статей.
    builder_reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    builder_reference_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
```

- [ ] **Step 2: Обновить тест на новые поля**

В `execution/backend/tests/test_models_site.py` добавить после `test_site_builder_parent_id` (эта функция там уже есть с прошлой сессии):

```python
def test_site_builder_reference_fields():
    """builder_reference_id/builder_reference_synced_at — то же самое, что
    reference_article_id/reference_synced_at у статей, но для эталонной
    карточки строителя (см. app/companies/reference.py)."""
    site = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
                builder_reference_id=77)
    assert site.builder_reference_id == 77
    assert site.builder_reference_synced_at is None
```

- [ ] **Step 3: Прогнать тест**

Run: `docker compose exec api pytest tests/test_models_site.py -v` (из `execution/`)
Expected: все тесты файла PASS, включая новый `test_site_builder_reference_fields`.

- [ ] **Step 4: Commit**

```bash
git add execution/backend/app/models/site.py execution/backend/tests/test_models_site.py
git commit -m "feat: поля builder_reference_id/builder_reference_synced_at на Site"
```

---

### Task 2: Alembic-миграция

**Files:**
- Create: `execution/backend/alembic/versions/e19ffe68c564_add_builder_reference_fields.py`

- [ ] **Step 1: Написать миграцию**

```python
"""add builder reference fields

builder_reference_id — id эталонной карточки строителя на самом сайте (по
образцу reference_article_id у статей); builder_reference_synced_at — когда
последний раз успешно синхронизирован builder_template_html из неё (см.
app/companies/reference.py::sync_builder_reference).

Revision ID: e19ffe68c564
Revises: 95858f3dd890
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e19ffe68c564'
down_revision: Union[str, None] = '95858f3dd890'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sites', sa.Column('builder_reference_id', sa.Integer(), nullable=True))
    op.add_column('sites', sa.Column('builder_reference_synced_at',
                                     sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('sites', 'builder_reference_synced_at')
    op.drop_column('sites', 'builder_reference_id')
```

- [ ] **Step 2: Применить локально и проверить**

Run (из `execution/`): `docker compose run --rm migrate`
Expected: `Running upgrade 95858f3dd890 -> e19ffe68c564, add builder reference fields`, без ошибок.

- [ ] **Step 3: Commit**

```bash
git add execution/backend/alembic/versions/e19ffe68c564_add_builder_reference_fields.py
git commit -m "feat: миграция — колонки builder_reference_id/builder_reference_synced_at"
```

---

### Task 3: `sync_builder_reference` — проверка контракта и кеш

**Files:**
- Create: `execution/backend/tests/test_companies_reference.py`
- Create: `execution/backend/app/companies/reference.py`

- [ ] **Step 1: Написать падающие тесты**

```python
import pytest

from app.companies.reference import sync_builder_reference
from app.sites.reference import ReferenceError

_VALID_TEMPLATE = (
    '<div id="builder">'
    '<h1 id="builder-main-title"></h1>'
    '<div id="builder-contacts">'
    '<div id="builder-contacts-grid">'
    '<div id="builder-contact-1"></div>'
    '</div></div></div>'
)


class FakeClient:
    def __init__(self, html=_VALID_TEMPLATE):
        self.html = html
        self.requested = []

    def get_page(self, page_id):
        self.requested.append(page_id)
        return {"id": page_id, "text": self.html}


@pytest.fixture
def site(db_session):
    from app.models.site import Site

    row = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
               builder_reference_id=77)
    db_session.add(row)
    db_session.commit()
    return row


def test_sync_caches_template_html(db_session, site):
    sync_builder_reference(db_session, site, FakeClient())
    assert "builder-main-title" in site.builder_template_html
    assert site.builder_reference_synced_at is not None


def test_sync_requires_reference_id(db_session, site):
    site.builder_reference_id = None
    db_session.commit()
    with pytest.raises(ReferenceError, match="Эталонная"):
        sync_builder_reference(db_session, site, FakeClient())


def test_sync_rejects_page_missing_main_title(db_session, site):
    html = _VALID_TEMPLATE.replace('id="builder-main-title"', 'id="something-else"')
    with pytest.raises(ReferenceError, match="builder-main-title"):
        sync_builder_reference(db_session, site, FakeClient(html=html))


def test_sync_rejects_page_missing_contacts_grid(db_session, site):
    html = '<div id="builder"><h1 id="builder-main-title"></h1></div>'
    with pytest.raises(ReferenceError, match="builder-contacts"):
        sync_builder_reference(db_session, site, FakeClient(html=html))


def test_sync_rejects_page_missing_contact_template_item(db_session, site):
    html = ('<div id="builder"><h1 id="builder-main-title"></h1>'
            '<div id="builder-contacts"><div id="builder-contacts-grid"></div></div></div>')
    with pytest.raises(ReferenceError, match="builder-contact-1"):
        sync_builder_reference(db_session, site, FakeClient(html=html))


def test_sync_failure_does_not_clobber_previous_cache(db_session, site):
    """Отказ повторной синхронизации не должен стирать кеш от прошлой
    успешной — иначе один плохой запуск оставляет сайт без шаблона вовсе."""
    sync_builder_reference(db_session, site, FakeClient())
    old_html = site.builder_template_html
    old_synced_at = site.builder_reference_synced_at

    with pytest.raises(ReferenceError):
        sync_builder_reference(db_session, site, FakeClient(html="<p>плохой</p>"))

    assert site.builder_template_html == old_html
    assert site.builder_reference_synced_at == old_synced_at
```

- [ ] **Step 2: Прогнать тесты — убедиться, что падают**

Run: `docker compose exec api pytest tests/test_companies_reference.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.companies.reference'`.

- [ ] **Step 3: Реализовать `app/companies/reference.py`**

```python
"""Синхронизация карточки сайта с эталонной страницей строителя — по образцу
app/sites/reference.py для статей, но с проверкой разметочного контракта
вместо счётчика картинок.

Отличие от эталона статьи принципиальное: reference_html статей — просто
стилевой образец для RouterAI (LLM генерирует новую разметку по мотивам),
любой HTML подходит. builder_template_html — жёсткий контракт: fill_builder_
template (app/companies/template.py) ищет в шаблоне строго определённые
id/class и подставляет в них текст детерминированно, без LLM. Эталонная
страница обязана быть уже собрана этим же движком (или вручную по тому же
контракту) — иначе синхронизация молча сохранит шаблон, который не сможет
ничего заполнить.
"""

from __future__ import annotations

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models.site import Site
from app.sites.reference import ReferenceError

# Единственные элементы, без которых fill_builder_template не соберёт карточку
# вообще. Блоки лого/о компании/специализации/преимуществ там же штатно
# необязательны (см. fill_about() и logo_img/logo_text_span в template.py) —
# их отсутствие в эталоне не повод для отказа.
_REQUIRED_MARKERS = ("builder-main-title", "builder-contacts", "builder-contacts-grid")


def _missing_markers(html: str) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    missing = [marker for marker in _REQUIRED_MARKERS if soup.find(id=marker) is None]
    grid = soup.find(id="builder-contacts-grid")
    if grid is not None and grid.find(id="builder-contact-1") is None:
        missing.append("builder-contact-1")
    return missing


def sync_builder_reference(db: Session, site: Site, client, commit: bool = True) -> None:
    """Тянет эталонную карточку строителя, заполняет builder_template_html.
    `commit=False` — тот же приём, что sync_site_reference: вызывающий
    (sync_site в app/api/admin_sites.py) сам решает, когда коммитить."""
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

- [ ] **Step 4: Прогнать тесты снова**

Run: `docker compose exec api pytest tests/test_companies_reference.py -v`
Expected: все 6 тестов PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/companies/reference.py execution/backend/tests/test_companies_reference.py
git commit -m "feat: sync_builder_reference — синхронизация шаблона строителя с проверкой контракта"
```

---

### Task 4: Понятный текст ошибки при несинхронизированном шаблоне

**Files:**
- Modify: `execution/backend/app/companies/builder.py:73-77`

- [ ] **Step 1: Переписать сообщение**

Заменить:

```python
    def _require_template(self) -> None:
        if not self.site.builder_template_html:
            raise SiteAPIError(
                "у сайта не задан шаблон карточки строителя — заполни "
                "builder_template_html на карточке сайта")
```

на:

```python
    def _require_template(self) -> None:
        if not self.site.builder_template_html:
            raise SiteAPIError(
                "у сайта не задан или не синхронизирован шаблон карточки "
                "строителя — укажи ID эталонной карточки в настройках сайта "
                "и нажми «Проверить и синхронизировать»")
```

(Старое сообщение отправляло редактировать поле `builder_template_html` напрямую в БД — этого поля в форме никогда не было, отсюда и жалоба, с которой начиналась эта задача.)

- [ ] **Step 2: Убедиться, что существующий тест не сломался**

Run: `docker compose exec api pytest tests/test_companies_builder.py::test_build_requires_builder_template -v`
Expected: PASS — тест проверяет `"шаблон" in company.error_text.lower()`, слово осталось.

- [ ] **Step 3: Commit**

```bash
git add execution/backend/app/companies/builder.py
git commit -m "fix: текст ошибки отсутствующего шаблона строителя — ссылается на синхронизацию, не на ручное поле"
```

---

### Task 5: `POST /admin/sites/{id}/sync` — два независимых шага

**Files:**
- Modify: `execution/backend/app/api/admin_sites.py`

- [ ] **Step 1: Обновить импорты**

Строки 1-16 заменить на:

```python
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.companies.reference import sync_builder_reference
from app.config import config
from app.models.site import Site
from app.models.user import User
from app.settings.crypto import SecretDecryptionError, decrypt, encrypt, mask
from app.sites.client import SiteAPIError, SiteClient
from app.sites.reference import ReferenceError, sync_site_reference
```

- [ ] **Step 2: Обновить `SiteIn`/`SiteOut`/`_to_out`**

Текущие строки 37-85 (класс `SiteIn` до конца `_to_out`) заменить целиком на:

```python
class SiteIn(BaseModel):
    name: str
    domain: str
    base_url: str
    api_token: str = ""            # пусто при обновлении = «не менять»
    is_active: bool = True
    site_description: str = ""
    tone_of_voice: str = ""
    publish_target: str = "pages"
    articles_parent_id: int | None = None
    reference_article_id: int | None = None
    image_style_prompt: str = ""
    cover_mode: str = "prompt"
    cover_style_prompt: str = ""
    builder_parent_id: int | None = None
    builder_reference_id: int | None = None


class SiteOut(SiteIn):
    id: int
    watermark_path: str = ""
    # Поля ниже заполняет синхронизация, руками они не редактируются — поэтому
    # их нет в SiteIn: пришедшее с фронта значение всё равно было бы затёрто.
    articles_url_prefix: str = ""
    reference_images: int = 0
    reference_synced_at: datetime | None = None
    builder_reference_synced_at: datetime | None = None


def _to_out(site: Site) -> SiteOut:
    try:
        token = decrypt(site.api_token_enc, config.encryption_key) if site.api_token_enc else ""
        shown = mask(token) if token else ""
    except SecretDecryptionError as exc:
        shown = f"ОШИБКА: {exc}"
    return SiteOut(
        id=site.id, name=site.name, domain=site.domain, base_url=site.base_url,
        api_token=shown, is_active=site.is_active,
        site_description=site.site_description, tone_of_voice=site.tone_of_voice,
        publish_target=site.publish_target, articles_parent_id=site.articles_parent_id,
        reference_article_id=site.reference_article_id,
        image_style_prompt=site.image_style_prompt,
        cover_mode=site.cover_mode, cover_style_prompt=site.cover_style_prompt,
        builder_parent_id=site.builder_parent_id,
        builder_reference_id=site.builder_reference_id,
        watermark_path=site.watermark_path,
        articles_url_prefix=site.articles_url_prefix,
        reference_images=site.reference_images,
        reference_synced_at=site.reference_synced_at,
        builder_reference_synced_at=site.builder_reference_synced_at,
    )
```

Функции `_apply`, `_get_or_404`, `open_client` и роуты `list_sites`/`create_site`/`update_site`/`delete_site` — без изменений, оставить как есть.

- [ ] **Step 3: Заменить `SyncResult` и `sync_site`**

Найти класс `SyncResult` (было строками 159-164) и функцию `sync_site` (было строками 167-207), заменить оба целиком на:

```python
class SyncResult(BaseModel):
    ok: bool = True

    articles_ok: bool | None = None   # None = раздел статей не сконфигурирован
    articles_detail: str = ""
    url_prefix: str = ""
    pages: int = 0
    reference_images: int = 0

    builder_ok: bool | None = None    # None = эталон строителя не сконфигурирован
    builder_detail: str = ""


def _sync_with_retries(db: Session, step) -> tuple[bool, str, Any]:
    """Общий цикл ретраев для обоих шагов синхронизации (эталон статьи,
    эталон строителя): 5xx и сетевые сбои (`SiteAPIError.status_code` — `None`
    или `>= 500`) повторяются до SYNC_MAX_RETRIES раз, остальное — сразу
    отказ. `step()` сам делает работу и сам коммитит на успехе — так отказ
    одного шага откатывает только его собственные незакоммиченные изменения,
    не трогая то, что уже успешно закоммитил другой, независимый шаг."""
    for attempt in range(SYNC_MAX_RETRIES):
        try:
            value = step()
        except (ReferenceError, SecretDecryptionError) as exc:
            db.rollback()
            return False, str(exc), None
        except SiteAPIError as exc:
            if not _sync_is_retryable(exc) or attempt == SYNC_MAX_RETRIES - 1:
                db.rollback()
                return False, str(exc), None
            time.sleep(SYNC_RETRY_BACKOFF * (2**attempt))
            continue
        return True, "", value
    return False, "не удалось синхронизировать", None  # недостижимо на практике


@router.post("/{site_id}/sync", response_model=SyncResult)
def sync_site(site_id: int, db: Session = Depends(get_db),
              _user: User = Depends(require_role("admin", "manager"))):
    """Одна кнопка проверяет всё сразу: токен, раздел статей и эталон
    строителя. Два шага независимы — у сайта может быть настроен только один
    из них, и отсутствие настройки для другого не роняет кнопку целиком (см.
    directions/2026-08-13-builder-reference-sync-design.md). Каждый шаг — своя
    транзакция: отказ одного не откатывает уже успешно закоммиченный другой
    (иначе рабочий эталон статей терялся бы из-за отказа синхронизации
    эталона строителя, никак с ним не связанного).

    Ошибки возвращаются телом с articles_ok=False / builder_ok=False, а не
    4xx: это диагностика чужого сайта, а не отказ нашего API — фронту нужно
    показать текст, а не свалиться в общий обработчик ошибок.
    """
    site = _get_or_404(db, site_id)
    result = SyncResult()
    articles_wanted = bool(site.articles_parent_id or site.reference_article_id)
    builder_wanted = bool(site.builder_reference_id)

    if not articles_wanted and not builder_wanted:
        return result

    try:
        client = open_client(db, site)
    except SecretDecryptionError as exc:
        if articles_wanted:
            result.articles_ok = False
            result.articles_detail = str(exc)
        if builder_wanted:
            result.builder_ok = False
            result.builder_detail = str(exc)
        result.ok = False
        return result

    if articles_wanted:
        def articles_step():
            sync_site_reference(db, site, client, commit=False)
            pages = client.list_section_pages(site.articles_url_prefix)
            db.commit()
            return len(pages)

        ok, detail, pages_count = _sync_with_retries(db, articles_step)
        result.articles_ok = ok
        result.articles_detail = detail
        if ok:
            result.url_prefix = site.articles_url_prefix
            result.pages = pages_count
            result.reference_images = site.reference_images

    if builder_wanted:
        def builder_step():
            sync_builder_reference(db, site, client, commit=False)
            db.commit()
            return None

        ok, detail, _value = _sync_with_retries(db, builder_step)
        result.builder_ok = ok
        result.builder_detail = detail

    result.ok = result.articles_ok is not False and result.builder_ok is not False
    return result
```

Роут `upload_watermark` ниже — без изменений.

- [ ] **Step 4: Прогнать существующие тесты — увидеть ожидаемые падения**

Run: `docker compose exec api pytest tests/test_api_sites.py -v`
Expected: FAIL на тестах, использующих старый ключ `detail` в ответе `/sync`
(`test_sync_fills_prefix_images_and_page_count`,
`test_sync_reports_api_failure_without_raising`,
`test_sync_reports_bad_reference_without_raising`,
`test_sync_does_not_retry_on_404`) — `KeyError` или несовпадение по `detail`
(в новом ответе поле называется `articles_detail`).

- [ ] **Step 5: Commit backend-часть отдельно от тестов**

```bash
git add execution/backend/app/api/admin_sites.py
git commit -m "feat: /admin/sites/{id}/sync — независимые шаги статей и строителя"
```

---

### Task 6: Обновить `test_api_sites.py`

**Files:**
- Modify: `execution/backend/tests/test_api_sites.py`

- [ ] **Step 1: Переименовать `detail` → `articles_detail` в трёх тестах**

`test_sync_reports_api_failure_without_raising` (было `assert "403" in body["detail"]`):

```python
def test_sync_reports_api_failure_without_raising(admin_client, site_payload, monkeypatch):
    from app.sites.client import SiteAPIError

    def boom(self, page_id):
        raise SiteAPIError("страница 25: HTTP 403: Forbidden")

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", boom)
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert "403" in body["articles_detail"]
```

`test_sync_reports_bad_reference_without_raising`:

```python
def test_sync_reports_bad_reference_without_raising(admin_client, site_payload, monkeypatch):
    patch_site_api(monkeypatch, reference_html="<p>текст без картинок</p>")
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert "ни одной картинки" in body["articles_detail"]
```

`test_sync_does_not_retry_on_404`:

```python
def test_sync_does_not_retry_on_404(admin_client, site_payload, monkeypatch):
    """404 — не тот id страницы. Повтор с тем же запросом даст тот же 404,
    поэтому счётчик вызовов обязан остаться на единице."""
    from app.sites.client import SiteAPIError

    calls = {"n": 0}

    def get_page(self, page_id):
        calls["n"] += 1
        raise SiteAPIError("страница 25: HTTP 404: Not Found", status_code=404)

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", get_page)
    monkeypatch.setattr("app.api.admin_sites.time.sleep", lambda seconds: None)

    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert "404" in body["articles_detail"]
    assert calls["n"] == 1
```

- [ ] **Step 2: Обновить точную проверку формы ответа**

`test_sync_fills_prefix_images_and_page_count`:

```python
def test_sync_fills_prefix_images_and_page_count(admin_client, site_payload, monkeypatch):
    patch_site_api(monkeypatch, reference_html="<p>t</p><img><img><img>")
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body == {
        "ok": True,
        "articles_ok": True, "articles_detail": "",
        "url_prefix": "/poleznye-stati/", "pages": 1, "reference_images": 3,
        "builder_ok": None, "builder_detail": "",
    }
```

- [ ] **Step 3: Добавить тесты шага строителей**

Добавить в конец файла:

```python
_VALID_BUILDER_TEMPLATE = (
    '<div id="builder">'
    '<h1 id="builder-main-title"></h1>'
    '<div id="builder-contacts">'
    '<div id="builder-contacts-grid">'
    '<div id="builder-contact-1"></div>'
    '</div></div></div>'
)


def test_sync_skips_builder_step_when_not_configured(admin_client, site_payload, monkeypatch):
    """site_payload не задаёт builder_reference_id — шаг строителей должен
    молча пропускаться, не мешая шагу статей."""
    patch_site_api(monkeypatch)
    site_id = admin_client.post("/api/admin/sites", json=site_payload).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is True
    assert body["builder_ok"] is None
    assert body["builder_detail"] == ""


def test_sync_fills_builder_template_when_configured(admin_client, site_payload, monkeypatch):
    def get_page(self, page_id):
        if page_id == 25:
            return {"id": 25, "url": "/poleznye-stati/"}
        if page_id == 77:
            return {"id": 77, "text": _VALID_BUILDER_TEMPLATE}
        return {"id": page_id, "text": "<img><img>"}

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", get_page)
    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages",
                        lambda self, prefix: [])

    site_id = admin_client.post(
        "/api/admin/sites", json={**site_payload, "builder_reference_id": 77}).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is True
    assert body["builder_ok"] is True
    assert body["builder_detail"] == ""


def test_sync_reports_builder_failure_independently_of_articles(admin_client, site_payload,
                                                                 monkeypatch):
    """Эталон статьи валиден, эталон строителя — нет: итог должен показать
    оба результата раздельно, не теряя успех статей за отказом строителей."""
    def get_page(self, page_id):
        if page_id == 25:
            return {"id": 25, "url": "/poleznye-stati/"}
        if page_id == 77:
            return {"id": 77, "text": "<p>не тот контракт</p>"}
        return {"id": page_id, "text": "<img><img>"}

    monkeypatch.setattr("app.api.admin_sites.SiteClient.get_page", get_page)
    monkeypatch.setattr("app.api.admin_sites.SiteClient.list_section_pages",
                        lambda self, prefix: [])

    site_id = admin_client.post(
        "/api/admin/sites", json={**site_payload, "builder_reference_id": 77}).json()["id"]
    body = admin_client.post(f"/api/admin/sites/{site_id}/sync").json()
    assert body["ok"] is False
    assert body["articles_ok"] is True
    assert body["builder_ok"] is False
    assert "builder-main-title" in body["builder_detail"]
```

- [ ] **Step 4: Прогнать весь файл**

Run: `docker compose exec api pytest tests/test_api_sites.py -v`
Expected: все тесты PASS (включая 3 новых).

- [ ] **Step 5: Прогнать весь бэкенд-набор**

Run: `docker compose exec api pytest -q`
Expected: все тесты PASS, без регрессий в других файлах.

- [ ] **Step 6: Commit**

```bash
git add execution/backend/tests/test_api_sites.py
git commit -m "test: /sync — раздельные articles_detail/builder_detail, независимость шага строителя"
```

---

### Task 7: Фронтенд — типы

**Files:**
- Modify: `execution/frontend/src/api.ts:71-82` (интерфейс `SiteFull`)
- Modify: `execution/frontend/src/api.ts:142-145` (`syncSite`)

- [ ] **Step 1: Обновить `SiteFull`**

Заменить:

```ts
export interface SiteFull {
  id: number; name: string; domain: string; base_url: string
  api_token: string; is_active: boolean; publish_target: string
  site_description: string; tone_of_voice: string
  articles_parent_id: number | null; reference_article_id: number | null
  image_style_prompt: string; cover_mode: string; cover_style_prompt: string
  builder_template_html: string; builder_parent_id: number | null
  watermark_path: string
  // Заполняются синхронизацией, в форме только читаются.
  articles_url_prefix: string; reference_images: number
  reference_synced_at: string | null
}
```

на:

```ts
export interface SiteFull {
  id: number; name: string; domain: string; base_url: string
  api_token: string; is_active: boolean; publish_target: string
  site_description: string; tone_of_voice: string
  articles_parent_id: number | null; reference_article_id: number | null
  image_style_prompt: string; cover_mode: string; cover_style_prompt: string
  builder_parent_id: number | null; builder_reference_id: number | null
  watermark_path: string
  // Заполняются синхронизацией, в форме только читаются.
  articles_url_prefix: string; reference_images: number
  reference_synced_at: string | null
  builder_reference_synced_at: string | null
}

export interface SyncResult {
  ok: boolean
  articles_ok: boolean | null; articles_detail: string
  url_prefix: string; pages: number; reference_images: number
  builder_ok: boolean | null; builder_detail: string
}
```

- [ ] **Step 2: Обновить `syncSite`**

Заменить:

```ts
export const syncSite = (id: number) =>
  api.post<{ ok: boolean; url_prefix: string; pages: number
             reference_images: number; detail: string }>(`/admin/sites/${id}/sync`)
    .then(r => r.data)
```

на:

```ts
export const syncSite = (id: number) =>
  api.post<SyncResult>(`/admin/sites/${id}/sync`).then(r => r.data)
```

- [ ] **Step 3: Проверить типы**

Run (из `execution/frontend/`): `npx tsc --noEmit -p tsconfig.json`
Expected: ошибки в `AdminSitesPage.tsx` (использует старые поля/форму `syncSite`) — это ожидаемо, чинится в Task 8. Если ошибки только в `AdminSitesPage.tsx` — этот шаг сделан верно.

- [ ] **Step 4: Commit**

```bash
git add execution/frontend/src/api.ts
git commit -m "feat: типы SiteFull/SyncResult — builder_reference_id вместо builder_template_html"
```

---

### Task 8: Фронтенд — форма, таблица, сообщение синхронизации

**Files:**
- Modify: `execution/frontend/src/pages/AdminSitesPage.tsx`

- [ ] **Step 1: Обновить `sync()`**

Заменить (строки 62-80):

```ts
  const sync = async (site: SiteFull) => {
    const result = await syncSite(site.id)
    if (result.ok) {
      message.success(`Раздел ${result.url_prefix}, статей в нём ${result.pages}, `
                      + `картинок в эталоне ${result.reference_images}`)
      load()
    } else {
      // Ошибка чужого сайта показывается целиком: администратору нужно понять,
      // что именно не так — токен, id раздела или id эталона.
      message.error(result.detail, 8)
    }
    // sync_site (app/api/admin_sites.py) сознательно всегда отвечает 200
    // с {ok, detail} для всех диагностируемых отказов чужого сайта (плохой
    // токен, не тот id эталона, недоступный раздел и т.п.) — именно чтобы
    // сюда пришёл предсказуемый результат, а не исключение. syncSite() может
    // отклониться только при по-настоящему неожиданном сбое (сеть, 500),
    // который уже покрыт глобальным интерцептором api.ts — отдельный
    // try/catch здесь не нужен.
  }
```

на:

```ts
  const sync = async (site: SiteFull) => {
    const result = await syncSite(site.id)
    // Шаги статей и строителя независимы (app/api/admin_sites.py::sync_site) —
    // у сайта может быть настроен только один из них. Собираем сообщение из
    // того, что реально сконфигурировано, а не из фиксированного набора полей.
    const parts: string[] = []
    if (result.articles_ok) {
      parts.push(`статьи: раздел ${result.url_prefix}, страниц ${result.pages}, `
                + `картинок в эталоне ${result.reference_images}`)
    } else if (result.articles_detail) {
      parts.push(`статьи: ${result.articles_detail}`)
    }
    if (result.builder_ok) {
      parts.push('строители: эталон синхронизирован')
    } else if (result.builder_detail) {
      parts.push(`строители: ${result.builder_detail}`)
    }
    const text = parts.join(' · ') || 'Нечего синхронизировать — не задан ни один эталон'
    ;(result.ok ? message.success : message.error)(text, 8)
    // load() — всегда, а не только при result.ok: шаги независимы, и один
    // мог успешно закоммититься, даже если другой упал (sync_site фиксирует
    // каждый шаг отдельной транзакцией) — без этого обновлённые колонки
    // «Раздел»/«Эталон» не покажут свежее состояние после частичного успеха.
    load()
  }
```

- [ ] **Step 2: Добавить колонку таблицы**

После колонки `'Эталон'` (строки 104-109) добавить:

```tsx
            {
              title: 'Эталон строителя', width: 170,
              render: (_, r: SiteFull) => r.builder_reference_synced_at
                ? dayjs(r.builder_reference_synced_at).format('DD.MM HH:mm')
                : '—',
            },
```

- [ ] **Step 3: Добавить поле формы**

Заменить блок `builder_parent_id` (строки 245-251):

```tsx
          <Form.Item name="builder_parent_id" label="ID родительской страницы для карточек строителей"
                     style={{ marginTop: 12 }}
                     extra="Страницы компаний из партий строителей создаются как дочерние
                            для этой страницы. Пока не заполнено — создание страниц компании
                            этого сайта завершится ошибкой.">
            <InputNumber style={{ width: '100%' }} placeholder="25" />
          </Form.Item>
```

на:

```tsx
          <Form.Item name="builder_parent_id" label="ID родительской страницы для карточек строителей"
                     style={{ marginTop: 12 }}
                     extra="Страницы компаний из партий строителей создаются как дочерние
                            для этой страницы. Пока не заполнено — создание страниц компании
                            этого сайта завершится ошибкой.">
            <InputNumber style={{ width: '100%' }} placeholder="25" />
          </Form.Item>
          <Form.Item name="builder_reference_id" label="ID эталонной карточки строителя"
                     extra={editing?.builder_reference_synced_at
                       ? `Синхронизирована ${dayjs(editing.builder_reference_synced_at)
                            .format('DD.MM.YYYY HH:mm')}`
                       : 'Разметка этой страницы (id/class-атрибуты builder-main-title, ' +
                        'builder-contacts и т.п.) — шаблон для всех карточек строителей ' +
                        'сайта; страница должна быть уже собрана этим сервисом или вручную ' +
                        'по тому же контракту. Без этого — «Проверить и синхронизировать».'}>
            <InputNumber style={{ width: '100%' }} placeholder="77" />
          </Form.Item>
```

- [ ] **Step 4: Проверить типы**

Run (из `execution/frontend/`): `npx tsc --noEmit -p tsconfig.json`
Expected: `EXIT 0`, без ошибок.

- [ ] **Step 5: Commit**

```bash
git add execution/frontend/src/pages/AdminSitesPage.tsx
git commit -m "feat: настройки сайта — поле эталона строителя, раздельный итог синхронизации"
```

---

### Task 9: Финальная проверка

**Files:** нет изменений — только верификация.

- [ ] **Step 1: Полный прогон бэкенд-тестов**

Run: `docker compose exec api pytest -q` (из `execution/`)
Expected: все тесты PASS, 0 failed.

- [ ] **Step 2: Полная проверка типов фронтенда**

Run: `npx tsc --noEmit -p tsconfig.json` (из `execution/frontend/`)
Expected: exit code 0.

- [ ] **Step 3: Собрать прод-бандл фронтенда**

Run: `npm run build` (из `execution/frontend/`)
Expected: сборка завершается без ошибок (`tsc && vite build` — тот же шаг, что запускается в Dockerfile прод-образа).

- [ ] **Step 4: Ручная проверка в браузере (dev-стек)**

1. Открыть настройки сайта, у которого уже есть карточка строителя, отдать ID её страницы в новое поле «ID эталонной карточки строителя».
2. Нажать «Проверить и синхронизировать» — убедиться, что тост показывает раздельно статус статей и строителей, а колонка «Эталон строителя» обновилась.
3. Запустить генерацию компании на этом сайте — убедиться, что ошибка «шаблон не задан» больше не возникает.

- [ ] **Step 5: Итоговый commit (если что-то осталось несохранённым)**

```bash
git status --short
# если есть незакоммиченное — добавить и закоммитить с понятным сообщением
```
