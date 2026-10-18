# Проекты домов — рерайт описаний — план реализации

> **Для исполнителей-агентов:** ОБЯЗАТЕЛЬНЫЙ ПОДСКИЛЛ: superpowers:subagent-driven-development (рекомендуется) или superpowers:executing-plans. Шаги отмечаются чекбоксами (`- [ ]`).

**Цель:** раздел панели «Проекты домов» — по кнопке «Запустить рерайт» переписывает через RouterAI блок «Описание проекта» на дочерних staticpages выбранной родительской страницы сайта и сразу записывает результат на сайт, сохраняя факты, разметку и объём.

**Архитектура:** проект — поля карточки сайта (`projects_enabled`, `projects_parent_id`), страницы и запуски — две новые таблицы. Запуск: синхронизация (карточка каждой staticpage сайта, отбор прямых детей родителя) → отбор по режиму «новые»/«все» → цепочка Celery-задач по одной странице (не больше одного слота воркера). Задача страницы находит блок `section.project-description div.product-info__text`, отдаёт модели только его, проверяет ответ кодом (объём ±10 %, числа, структура, отличие от оригинала) и вставляет его обратно по позициям в исходной строке — остальной HTML страницы не меняется.

**Стек:** FastAPI, SQLAlchemy 2, Alembic, Celery + Redis, requests; React 18 + antd 5.

**Дизайн:** `directions/2026-10-18-house-projects-rewrite-design.md` — читать перед началом.

**Происхождение кода:** весь код и все тесты этого плана прогнаны до записи плана на копии репозитория от `main` (73e4139): полный регресс бэкенда — 964 теста зелёные, миграция с нуля, откат и `alembic check` на Postgres 16 — без расхождений, `npm run build` (с `tsc`) проходит. Модуль разметки проверен на всех 56 страницах проектов stroybaza-samara.ru (блок найден везде, вставка без изменений возвращает страницу байт в байт), промпт и пороги — на 5 страницах с `openai/gpt-6-luna-pro` (см. дизайн, «Проба до реализации»). Копируйте блоки дословно; если тест не проходит — это дефект реализации или среды, а не повод править тест. Если всё же нашли дефект в самом плане — правка идёт в ДВА места: код и текст плана.

**Доработки по ревью при исполнении (2026-10-18).** Блоки кода ниже обновлены до итогового состояния; что изменилось против первой редакции и почему:
- `same_html` сравнивает с раскрытыми HTML-сущностями — сайт хранит часть описаний с `&nbsp;` (7 из 56 страниц Самары), и пересохранение редактором (`«` → `&laquo;`) иначе считалось бы ручной правкой и подменяло оригинал нашим рерайтом (ревью Task 2);
- `clean_model_html` берёт только содержимое блока ``` (в любом регистре), пояснения до и после блока отбрасываются; `validate_rewrite` отклоняет ответ, который не начинается с `<` и не кончается `>` — иначе «Вот текст:» уходил бы на сайт (ревью Task 2, правило — в Task 3).

**Правки существующих файлов** даны дифами относительно `main` — переносите их построчно (или `git apply`, если дифф скопирован в файл целиком). Новые файлы даны целиком.

---

## Структура файлов

```
execution/backend/
  app/models/site.py                            Modify: projects_enabled, projects_parent_id, projects_parent_title
  app/models/house_project.py                   Create: HouseProjectPage, ProjectRewriteRun
  app/models/__init__.py                        Modify: регистрация моделей
  alembic/versions/b5e1d8c3a9f2_house_projects.py Create
  app/house_projects/__init__.py                Create (пустой)
  app/house_projects/markup.py                  Create: поиск и замена блока описания, видимый текст
  app/house_projects/validate.py                Create: проверка рерайта против оригинала
  app/ai/prompts.py                             Modify: PROMPT_KEYS, PROMPT_VARIABLES
  app/seed.py                                   Modify: дефолтный промпт project_rewrite
  app/sites/client.py                           Modify: update_page_text(..., as_is=True)
  app/house_projects/sync.py                    Create: синхронизация страниц раздела, with_retries
  app/house_projects/runs.py                    Create: отбор по режиму, прогресс, зависшие, оборванные
  app/house_projects/rewriter.py                Create: рерайт одной страницы
  app/tasks.py                                  Modify: start_rewrite_run, rewrite_project_page
  app/api/house_projects.py                     Create: API раздела
  app/main.py                                   Modify: подключение роутера
  tests/…                                       Create/Modify: по задачам

execution/frontend/src/
  api.ts                                        Modify: типы и вызовы
  statuses.ts                                   Modify: HOUSE_PROJECT_PAGE_STATUS
  App.tsx                                       Modify: пункт меню и маршрут
  pages/JobsPage.tsx                            Modify: подписи задач журнала
  pages/AdminPromptsPage.tsx                    Modify: промпт project_rewrite
  pages/HouseProjectsPage.tsx                   Create: проекты
  pages/HouseProjectPages.tsx                   Create: страницы проекта и карточка
  help.tsx                                      Modify: справка раздела
  changelog.ts                                  Modify: запись в «Доработки»
```

## Как запускать

Все команды — из `execution/`.

```bash
docker compose run --rm --no-deps backend pytest -q tests/test_x.py   # тесты на SQLite в памяти
docker compose run --rm --no-deps backend pytest -q                   # полный регресс (~2,5 мин)
docker compose run --rm frontend sh -c "npm install && npm run build" # сборка фронта с tsc
```

Работа — в отдельном ворктри от `main` (скилл superpowers:using-git-worktrees), ветка `feature/house-projects`.

---

### Task 1: Модели и миграция

**Files:**
- Modify: `execution/backend/app/models/site.py`
- Create: `execution/backend/app/models/house_project.py`
- Modify: `execution/backend/app/models/__init__.py`
- Create: `execution/backend/alembic/versions/b5e1d8c3a9f2_house_projects.py`
- Test: `execution/backend/tests/test_models_house_project.py`

- [ ] **Step 1: Тест (красный)**

`execution/backend/tests/test_models_house_project.py`:

````python
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.house_project import HouseProjectPage, ProjectRewriteRun
from app.models.site import Site


@pytest.fixture
def site(db_session):
    row = Site(name="Стройбаза Самара", domain="stroybaza-samara.ru",
               base_url="https://stroybaza-samara.ru", api_token_enc="e")
    db_session.add(row)
    db_session.commit()
    return row


def test_site_projects_defaults(site):
    assert (site.projects_enabled, site.projects_parent_id, site.projects_parent_title) == \
        (False, None, "")


def test_page_defaults(db_session, site):
    page = HouseProjectPage(site_id=site.id, remote_id=16)
    db_session.add(page)
    db_session.commit()
    assert (page.status, page.original_html, page.rewritten_html, page.rewrite_count,
            page.rewritten_at, page.skip_reason) == ("new", "", "", 0, None, "")
    assert page.updated_at is not None


def test_page_unique_per_site(db_session, site):
    db_session.add_all([HouseProjectPage(site_id=site.id, remote_id=16),
                        HouseProjectPage(site_id=site.id, remote_id=16)])
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_run_defaults(db_session, site):
    run = ProjectRewriteRun(site_id=site.id)
    db_session.add(run)
    db_session.commit()
    assert (run.mode, run.total, run.error_text, run.finished_at) == ("new", 0, "", None)
    assert run.started_at is not None
````

- [ ] **Step 2: Запустить — падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_models_house_project.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.house_project'`

- [ ] **Step 3: Модели и миграция**

`execution/backend/app/models/site.py` — правка (дифф относительно main):

````diff
--- a/execution/backend/app/models/site.py
+++ b/execution/backend/app/models/site.py
@@ -99,3 +99,11 @@ class Site(Base):
     meta_without_city: Mapped[bool] = mapped_column(Boolean, default=False)
     wordstat_region_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
     brand: Mapped[str] = mapped_column(String(200), default="")
+
+    # --- проекты домов (directions/2026-10-18-house-projects-rewrite-design.md) ---
+    projects_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
+    # Родительская staticpage раздела; страницы проектов — её прямые дети.
+    # Url у детей свой (/pr/...), поэтому раздел задаётся id, а не префиксом.
+    projects_parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
+    # Название родителя на момент добавления — менеджер сверяет, что не ошибся с id.
+    projects_parent_title: Mapped[str] = mapped_column(String(300), default="")
````

`execution/backend/app/models/house_project.py`:

````python
"""Проекты домов: страницы раздела и запуски рерайта.
Дизайн: directions/2026-10-18-house-projects-rewrite-design.md."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.db import Base


class HouseProjectPage(Base):
    """Страница проекта дома на сайте и её описание.

    status: new (ещё не переписывалась) → queued → in_work → done | failed;
    skipped — нет блока описания или страница пропала с сайта (skip_reason)."""

    __tablename__ = "house_project_pages"
    __table_args__ = (
        UniqueConstraint("site_id", "remote_id", name="uq_house_project_pages_site_remote"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    remote_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    # Содержимое блока описания до нашего первого рерайта — все повторы от него.
    # Правка описания руками в админке сайта становится новым оригиналом.
    original_html: Mapped[str] = mapped_column(Text, default="")
    rewritten_html: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="new")
    skip_reason: Mapped[str] = mapped_column(String(100), default="")
    error_text: Mapped[str] = mapped_column(Text, default="")
    rewritten_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rewrite_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectRewriteRun(Base):
    """Запуск «Запустить рерайт» по проекту. Открыт, пока finished_at пуст."""

    __tablename__ = "project_rewrite_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    job_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # new — новые и с ошибкой; all — все страницы заново.
    mode: Mapped[str] = mapped_column(String(10), default="new")
    # 0, пока идёт синхронизация: страницы ещё не отобраны.
    total: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
````

`execution/backend/app/models/__init__.py` — правка (дифф относительно main):

````diff
--- a/execution/backend/app/models/__init__.py
+++ b/execution/backend/app/models/__init__.py
@@ -3,6 +3,7 @@ from app.models.category_meta import CategoryMeta, MetaRun, WordstatCache, Words
 from app.models.company import (
     Company, CompanyBatch, CompanyCandidate, CompanyImport, CompanyInfo,
 )
+from app.models.house_project import HouseProjectPage, ProjectRewriteRun
 from app.models.job import JobRun, LlmUsage
 from app.models.prompt_template import PromptTemplate
 from app.models.setting import Setting
@@ -16,6 +17,7 @@ __all__ = [
     "Article", "ArticleBatch", "ArticleImage",
     "CategoryMeta", "MetaRun", "WordstatCache", "WordstatCall",
     "Company", "CompanyBatch", "CompanyCandidate", "CompanyImport", "CompanyInfo",
+    "HouseProjectPage", "ProjectRewriteRun",
     "JobRun", "LlmUsage",
     "PromptTemplate", "Setting", "Site", "User",
 ]
````

`execution/backend/alembic/versions/b5e1d8c3a9f2_house_projects.py`:

````python
"""house projects: pages of the section and rewrite runs

Revision ID: b5e1d8c3a9f2
Revises: a7c4e2f9d1b3
Create Date: 2026-10-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5e1d8c3a9f2'
down_revision: Union[str, None] = 'a7c4e2f9d1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sites', sa.Column('projects_enabled', sa.Boolean(), nullable=False,
                                     server_default=sa.false()))
    op.add_column('sites', sa.Column('projects_parent_id', sa.Integer(), nullable=True))
    op.add_column('sites', sa.Column('projects_parent_title', sa.String(length=300),
                                     nullable=False, server_default=''))
    op.create_table(
        'house_project_pages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=False),
        sa.Column('remote_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('url', sa.String(length=500), nullable=False),
        sa.Column('original_html', sa.Text(), nullable=False),
        sa.Column('rewritten_html', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('skip_reason', sa.String(length=100), nullable=False),
        sa.Column('error_text', sa.Text(), nullable=False),
        sa.Column('rewritten_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rewrite_count', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('site_id', 'remote_id', name='uq_house_project_pages_site_remote'),
    )
    op.create_index(op.f('ix_house_project_pages_site_id'), 'house_project_pages', ['site_id'],
                    unique=False)
    op.create_table(
        'project_rewrite_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=False),
        sa.Column('job_run_id', sa.Integer(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('error_text', sa.Text(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['job_run_id'], ['job_runs.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_project_rewrite_runs_site_id'), 'project_rewrite_runs', ['site_id'],
                    unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_project_rewrite_runs_site_id'), table_name='project_rewrite_runs')
    op.drop_table('project_rewrite_runs')
    op.drop_index(op.f('ix_house_project_pages_site_id'), table_name='house_project_pages')
    op.drop_table('house_project_pages')
    op.drop_column('sites', 'projects_parent_title')
    op.drop_column('sites', 'projects_parent_id')
    op.drop_column('sites', 'projects_enabled')
````

- [ ] **Step 4: Запустить — проходит**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_models_house_project.py`
Expected: `4 passed`

- [ ] **Step 5: Миграция на Postgres**

```bash
docker compose up -d postgres
docker compose exec postgres psql -U app -d content -c "create database hp_check"
docker compose run --rm --no-deps -e DATABASE_URL=postgresql+psycopg://app:app@postgres:5432/hp_check backend \
  sh -c "alembic upgrade head && alembic check && alembic downgrade -1 && alembic upgrade head"
docker compose exec postgres psql -U app -d content -c "drop database hp_check"
```

Expected: `Running upgrade a7c4e2f9d1b3 -> b5e1d8c3a9f2`, затем `No new upgrade operations detected.`, откат и повторный накат без ошибок.

- [ ] **Commit**

```bash
git add execution/backend/app/models/site.py execution/backend/app/models/house_project.py execution/backend/app/models/__init__.py execution/backend/alembic/versions/b5e1d8c3a9f2_house_projects.py execution/backend/tests/test_models_house_project.py
git commit -m "feat: проекты домов — модели страниц и запусков рерайта

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 2: Блок описания на странице

Поиск `div.product-info__text` внутри `section.project-description` и замена его содержимого по позициям в исходной строке. Всё вне блока — байт в байт: разметка страниц двух видов, и пересериализация HTML переписала бы страницу целиком.

**Files:**
- Create: `execution/backend/app/house_projects/__init__.py` (пустой)
- Create: `execution/backend/app/house_projects/markup.py`
- Test: `execution/backend/tests/test_house_projects_markup.py`

- [ ] **Step 1: Тест (красный)**

`execution/backend/tests/test_house_projects_markup.py`:

````python
from app.house_projects.markup import (
    clean_model_html, find_description, plain_text, replace_description, same_html,
)

# Два вида разметки с сайта (stroybaza-samara.ru, страницы 16 и 189), сокращены.
INDENTED = """<section class="static-project">
<!-- Хар-ки и описание -->
<section class="product-info__section product-info__chars project-chars">
  <ul class="char"><li><span class="char-text">Площадь, м²</span><span class="char-text">82</span></li></ul>
</section>
<section class="product-info">
  <div class="product-info__main">
    <section class="product-info__section product-info__description project-description">
      <h2>Описание <br>проекта</h2>
      <div class="amk-projects__about-1 product-info__text">
        <p>Дом площадью 82.64 м² с террасой 9 м².</p><br>
        <h3>Ключевые преимущества <br>и удобства:</h3>
        <ul>
          <li>Монолитная плита.</li>
        </ul>
      </div>
    </section>
  </div>
</section>
<section class="cost-building"><div class="amk-projects__about-1" id="about-block-1">
{% custom_form "forma-rasschet" %}
</div></section>
</section>"""

ONE_LINE = ('<section class="static-project"><section class="product-info__section '
            'product-info__description project-description"><h2>Описание<br />\nпроекта</h2>'
            '<div class="amk-projects__about-1 product-info__text"><p>Каркасный дом 11×9 м.</p>'
            '<div class="note"><p>вложенный</p></div></div></section>'
            '<div class="buttons-columns"><a href="/addresses/projects/">Все проекты</a></div>'
            '</section>')


def test_finds_inner_html_of_description():
    block = find_description(INDENTED)
    assert block is not None
    assert block.inner_html.strip().startswith("<p>Дом площадью 82.64 м²")
    assert block.inner_html.strip().endswith("</ul>")


def test_nested_div_does_not_end_block_early():
    block = find_description(ONE_LINE)
    assert block.inner_html == ('<p>Каркасный дом 11×9 м.</p>'
                                '<div class="note"><p>вложенный</p></div>')


def test_replace_keeps_everything_outside_block_byte_for_byte():
    for page in (INDENTED, ONE_LINE):
        block = find_description(page)
        result = replace_description(page, block, "<p>Новый текст.</p>")
        assert result[:block.start] == page[:block.start]
        assert result.endswith(page[block.end:])
        assert find_description(result).inner_html == "\n<p>Новый текст.</p>\n"
    assert '{% custom_form "forma-rasschet" %}' in replace_description(
        INDENTED, find_description(INDENTED), "<p>x</p>")


def test_no_block_or_two_blocks_is_none():
    assert find_description("<section><p>нет описания</p></section>") is None
    twice = INDENTED.replace('<section class="cost-building">',
                             '<div class="product-info__text"><p>ещё</p></div>'
                             '<section class="cost-building">')
    assert find_description(twice) is None
    no_section = ONE_LINE.replace("project-description", "other")
    assert find_description(no_section) is None


def test_unclosed_block_is_none():
    assert find_description('<section class="project-description">'
                            '<div class="product-info__text"><p>обрыв') is None


def test_block_before_section_is_none():
    page = ('<div class="product-info__text"><p>x</p></div>'
            '<section class="project-description"><p>y</p></section>')
    assert find_description(page) is None


def test_clean_model_html_strips_fence():
    assert clean_model_html("```html\n<p>Текст</p>\n```") == "<p>Текст</p>"
    assert clean_model_html("Вот текст:\n```HTML\n<p>Текст</p>\n```\nГотово.") == "<p>Текст</p>"
    assert clean_model_html("  <p>Текст</p> ") == "<p>Текст</p>"
    assert clean_model_html(None) == ""


def test_plain_text_and_same_html():
    assert plain_text("<p>Дом&nbsp;82 м²</p>\n<ul><li>плита</li></ul>") == "Дом 82 м² плита"
    assert same_html("<p>a</p>\n  <p>b</p>", "<p>a</p><p>b</p>")
    assert same_html("<p>a<br />b</p>", "<p>a<br>b</p>")
    assert same_html("<p>«a» 11×9 b\xa0c</p>", "<p>&laquo;a&raquo; 11&times;9 b&nbsp;c</p>")
    assert not same_html("<p>a b</p>", "<p>ab</p>")
    assert not same_html("<p>a</p>", "<p>b</p>")
````

- [ ] **Step 2: Запустить — падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_house_projects_markup.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.house_projects'`

- [ ] **Step 3: Реализация**

Создать пустой `execution/backend/app/house_projects/__init__.py`.

`execution/backend/app/house_projects/markup.py`:

````python
"""Блок описания на странице проекта дома: поиск, замена, видимый текст.

Описание — содержимое `div.product-info__text` внутри
`section.project-description`. Меняется только оно, и по позициям в исходной
строке, без пересериализации HTML: разметка страниц бывает двух видов (с
отступами и в одну строку, `<br />` вместо `<br>`), и любой парсер, который
собирает HTML обратно, переписал бы страницу целиком — вместе с характеристиками
и шаблонным тегом `{% custom_form %}`."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

_OPEN_TAG = r"<{tag}\b[^>]*>"
_CLASS = re.compile(r"""\bclass\s*=\s*(["'])(.*?)\1""", re.DOTALL)
_DIV_TOKEN = re.compile(r"<div\b[^>]*>|</div\s*>", re.IGNORECASE)
_FENCED = re.compile(r"```[\w-]*[ \t]*\r?\n?(.*?)\r?\n?[ \t]*```", re.DOTALL)


@dataclass
class DescriptionBlock:
    start: int        # первый символ содержимого — сразу после открывающего <div>
    end: int          # позиция закрывающего </div>
    inner_html: str


def _with_class(page_html: str, tag: str, css_class: str) -> list[re.Match]:
    found = []
    for match in re.finditer(_OPEN_TAG.format(tag=tag), page_html, re.IGNORECASE):
        attr = _CLASS.search(match.group(0))
        if attr and css_class in attr.group(2).split():
            found.append(match)
    return found


def _closing_div(page_html: str, start: int) -> int | None:
    """Позиция </div>, закрывающего div, содержимое которого начинается со start."""
    depth = 1
    for token in _DIV_TOKEN.finditer(page_html, start):
        depth += -1 if token.group(0).startswith("</") else 1
        if depth == 0:
            return token.start()
    return None


def find_description(page_html: str) -> DescriptionBlock | None:
    """None — блока нет или их больше одного: какой из них описание, не угадываем."""
    sections = _with_class(page_html, "section", "project-description")
    blocks = _with_class(page_html, "div", "product-info__text")
    if len(sections) != 1 or len(blocks) != 1 or blocks[0].start() < sections[0].end():
        return None
    start = blocks[0].end()
    end = _closing_div(page_html, start)
    if end is None:
        return None
    return DescriptionBlock(start=start, end=end, inner_html=page_html[start:end])


def replace_description(page_html: str, block: DescriptionBlock, new_inner: str) -> str:
    return page_html[:block.start] + "\n" + new_inner.strip() + "\n" + page_html[block.end:]


def clean_model_html(raw: object) -> str:
    """Если модель обернула ответ в ```html, берётся только содержимое блока:
    пояснения до и после него на сайт не попадают. Остальное оценивает проверка."""
    text = str(raw or "").strip()
    fenced = _FENCED.search(text)
    return (fenced.group(1) if fenced else text).strip()


def plain_text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _normalized(fragment: str) -> str:
    fragment = html.unescape(fragment)
    fragment = re.sub(r">\s+<", "><", fragment)
    fragment = re.sub(r"<br\s*/?>", "<br>", fragment, flags=re.IGNORECASE)
    return " ".join(fragment.split())


def same_html(first: str, second: str) -> bool:
    """Совпадение без учёта пробелов, переносов между тегами, `<br />` против
    `<br>` и сущностей (`&nbsp;`, `&laquo;`) против самих символов: админка
    сайта переформатирует разметку при сохранении, и это не правка текста."""
    return _normalized(first) == _normalized(second)
````

- [ ] **Step 4: Запустить — проходит**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_house_projects_markup.py`
Expected: `8 passed`

- [ ] **Commit**

```bash
git add execution/backend/app/house_projects/__init__.py execution/backend/app/house_projects/markup.py execution/backend/tests/test_house_projects_markup.py
git commit -m "feat: проекты домов — поиск и замена блока описания без пересборки HTML

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 3: Проверка рерайта

Ответ — только HTML (без пояснений до и после), объём ±10 %, все числа оригинала (`82.64` = `82,64`, `11×9` — два числа), те же `h3` и пункты списков, теги только из оригинала, совпадение 4-словных цепочек не больше 25 %.

**Files:**
- Create: `execution/backend/app/house_projects/validate.py`
- Test: `execution/backend/tests/test_house_projects_validate.py`

- [ ] **Step 1: Тест (красный)**

`execution/backend/tests/test_house_projects_validate.py`:

````python
from app.house_projects.validate import (
    list_sizes, numbers, shared_shingles, validate_rewrite, volume_range,
)

ORIGINAL = ("<p>Представляем проект уютного дома площадью 82.64 м², идеально подходящего "
            "для семьи из четырёх человек.</p><br>"
            "<h3>Ключевые преимущества <br>и удобства:</h3>"
            "<ul><li>Монолитная плита под всем домом.</li>"
            "<li>Терраса 9.06 м² для отдыха.</li></ul>")
REWRITE = ("<p>Перед вами комфортный дом на 82,64 м², который отлично подойдёт "
           "семье, где растут двое детей.</p><br>"
           "<h3>Почему выбирают <br>этот проект:</h3>"
           "<ul><li>Под зданием залита монолитная плита.</li>"
           "<li>Для отдыха есть терраса 9.06 м².</li></ul>")


def test_good_rewrite_passes():
    assert validate_rewrite(ORIGINAL, REWRITE) == []


def test_numbers_normalize_comma_and_split_dimensions():
    assert numbers("<p>82,64 м² и 11×9 м</p>") == {"82.64", "11", "9"}
    assert numbers("<p>82.64</p>") == numbers("<p>82,64</p>")


def test_missing_number_is_reported():
    errors = validate_rewrite(ORIGINAL, REWRITE.replace("9.06", "девять"))
    assert any("пропали числа из оригинала: 9.06" in e for e in errors)


def test_volume_bounds():
    low, high = volume_range("<p>" + "а" * 100 + "</p>")
    assert (low, high) == (90, 110)
    original = "<p>" + "слово " * 50 + "</p>"
    assert any("объём" in e for e in validate_rewrite(original, "<p>коротко</p>"))


def test_structure_changes_are_reported():
    no_heading = REWRITE.replace("<h3>Почему выбирают <br>этот проект:</h3>",
                                 "<p>Почему выбирают этот проект:</p>")
    assert any("подзаголовков <h3> 0, в оригинале 1" in e
               for e in validate_rewrite(ORIGINAL, no_heading))
    merged = REWRITE.replace("</li><li>", " ")
    assert any("пунктов в списках [1], в оригинале [2]" in e
               for e in validate_rewrite(ORIGINAL, merged))
    assert list_sizes("<p>без списков</p>") == []


def test_text_around_html_is_reported():
    errors = validate_rewrite(ORIGINAL, "Вот вариант:\n" + REWRITE)
    assert any("не чистый HTML" in e for e in errors)
    assert not any("не чистый HTML" in e for e in validate_rewrite(ORIGINAL, " " + REWRITE + "\n"))


def test_foreign_tag_is_reported():
    errors = validate_rewrite(ORIGINAL, REWRITE.replace("<p>Перед", '<p><a href="/">Перед</a>'))
    assert any("теги, которых нет в оригинале: a" in e for e in errors)


def test_copy_is_too_close():
    assert shared_shingles(ORIGINAL, ORIGINAL) == 1.0
    errors = validate_rewrite(ORIGINAL, ORIGINAL)
    assert any("слишком близко к оригиналу: совпадает 100 %" in e for e in errors)
    assert shared_shingles("<p>два слова</p>", "<p>два слова</p>") == 0.0
````

- [ ] **Step 2: Запустить — падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_house_projects_validate.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.house_projects.validate'`

- [ ] **Step 3: Реализация**

`execution/backend/app/house_projects/validate.py`:

````python
"""Проверка рерайта описания проекта против оригинала.

Факты о проекте код сверяет по числам — их можно проверить надёжно: площади,
размеры, этажность. Материалы и конструктив («монолитная плита») держатся на
запрете в промпте. Нарушения уходят модели списком на второй попытке."""

from __future__ import annotations

import re

from app.house_projects.markup import plain_text

VOLUME_TOLERANCE = 0.10      # владелец: объём ±5–10 %
SHINGLE_WORDS = 4
MAX_SHARED_SHINGLES = 0.25   # доля 4-словных цепочек оригинала, допустимая в рерайте

_NUMBER = re.compile(r"[0-9]+(?:[.,][0-9]+)?")
_TAG_NAME = re.compile(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)")
_LIST = re.compile(r"<(ul|ol)\b[^>]*>(.*?)</\1\s*>", re.DOTALL | re.IGNORECASE)
_WORD = re.compile(r"\w+")


def numbers(fragment: str) -> set[str]:
    """«82.64» и «82,64» — одно число; «11×9» — два."""
    return {value.replace(",", ".") for value in _NUMBER.findall(plain_text(fragment))}


def tag_names(fragment: str) -> set[str]:
    return {name.lower() for name in _TAG_NAME.findall(fragment)}


def heading_count(fragment: str) -> int:
    return len(re.findall(r"<h3\b", fragment, re.IGNORECASE))


def list_sizes(fragment: str) -> list[int]:
    return [len(re.findall(r"<li\b", body, re.IGNORECASE)) for _, body in _LIST.findall(fragment)]


def shared_shingles(original: str, rewritten: str) -> float:
    """Доля цепочек из SHINGLE_WORDS слов оригинала, повторённых в рерайте."""
    def shingles(fragment: str) -> set[tuple[str, ...]]:
        words = [w.casefold() for w in _WORD.findall(plain_text(fragment))]
        return {tuple(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}

    source = shingles(original)
    if not source:
        return 0.0
    return len(source & shingles(rewritten)) / len(source)


def volume_range(original: str) -> tuple[int, int]:
    size = len(plain_text(original))
    return round(size * (1 - VOLUME_TOLERANCE)), round(size * (1 + VOLUME_TOLERANCE))


def validate_rewrite(original: str, rewritten: str) -> list[str]:
    errors: list[str] = []
    stripped = rewritten.strip()
    if not (stripped.startswith("<") and stripped.endswith(">")):
        errors.append("ответ — не чистый HTML: верни только разметку описания, "
                      "без пояснений до и после")
    low, high = volume_range(original)
    size = len(plain_text(rewritten))
    if not low <= size <= high:
        errors.append(f"объём {size} символов без тегов, нужно {low}–{high}")
    missing = sorted(numbers(original) - numbers(rewritten), key=lambda v: float(v))
    if missing:
        errors.append(f"пропали числа из оригинала: {', '.join(missing)} — верни их как были")
    if heading_count(rewritten) != heading_count(original):
        errors.append(f"подзаголовков <h3> {heading_count(rewritten)}, "
                      f"в оригинале {heading_count(original)}")
    if list_sizes(rewritten) != list_sizes(original):
        errors.append(f"пунктов в списках {list_sizes(rewritten) or 'нет'}, "
                      f"в оригинале {list_sizes(original) or 'нет'}")
    extra = sorted(tag_names(rewritten) - tag_names(original))
    if extra:
        errors.append(f"теги, которых нет в оригинале: {', '.join(extra)}")
    shared = shared_shingles(original, rewritten)
    if shared > MAX_SHARED_SHINGLES:
        errors.append(f"слишком близко к оригиналу: совпадает {round(shared * 100)} % "
                      f"цепочек из {SHINGLE_WORDS} слов, допустимо до "
                      f"{round(MAX_SHARED_SHINGLES * 100)} % — перестрой фразы")
    return errors
````

- [ ] **Step 4: Запустить — проходит**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_house_projects_validate.py`
Expected: `8 passed`

- [ ] **Commit**

```bash
git add execution/backend/app/house_projects/validate.py execution/backend/tests/test_house_projects_validate.py
git commit -m "feat: проекты домов — проверка рерайта: объём, числа, структура, отличие от оригинала

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 4: Промпт `project_rewrite`

**Files:**
- Modify: `execution/backend/app/ai/prompts.py`
- Modify: `execution/backend/app/seed.py`
- Modify: `execution/backend/tests/test_ai_prompts.py`, `execution/backend/tests/test_api_admin_prompts.py`

- [ ] **Step 1: Тесты (красные)**

`execution/backend/tests/test_ai_prompts.py` — правка (дифф относительно main):

````diff
--- a/execution/backend/tests/test_ai_prompts.py
+++ b/execution/backend/tests/test_ai_prompts.py
@@ -119,6 +119,11 @@ def test_default_prompts_render_with_real_contexts(db_session):
                               "phrases": ["фанера — 96275"],
                               "products": ["Фанера ФК 1525х1525х10"],
                               "violations": ["в SEO-тексте нет «в Москве»"]},
+        "project_rewrite": {"site_name": "X", "tone_of_voice": "тон",
+                            "title": "Проект дома из кирпича от 80 м²",
+                            "description_html": "<p>Дом площадью 82.64 м².</p>",
+                            "char_count": 2180,
+                            "violations": ["пропали числа из оригинала: 9.06"]},
     }
     for key in PROMPT_KEYS:
         # PROMPT_VARIABLES — то, по чему check_template судит о шаблоне из
````

`execution/backend/tests/test_api_admin_prompts.py` — правка (дифф относительно main):

````diff
--- a/execution/backend/tests/test_api_admin_prompts.py
+++ b/execution/backend/tests/test_api_admin_prompts.py
@@ -38,7 +38,7 @@ def test_admin_lists_global_prompts(admin_client, seeded):
     keys = {item["key"] for item in body if item["site_id"] is None}
     assert keys == {"topics", "article_body", "article_label", "cover", "content_image",
                     "builder_text", "category_seeds", "category_variants", "category_meta",
-                    "category_seo_text"}
+                    "category_seo_text", "project_rewrite"}
 
 
 def test_admin_saves_site_override(admin_client, seeded, db_session, site):
````

- [ ] **Step 2: Запустить — падают**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_ai_prompts.py tests/test_api_admin_prompts.py`
Expected: FAIL — `test_admin_lists_global_prompts`: `Extra items in the right set: 'project_rewrite'`. `test_ai_prompts.py` пока проходит — новый контекст начнёт проверяться, когда ключ появится в `PROMPT_KEYS`; он ловит расхождение переменных шаблона и контекста после Step 3.

- [ ] **Step 3: Реализация**

`execution/backend/app/ai/prompts.py` — правка (дифф относительно main):

````diff
--- a/execution/backend/app/ai/prompts.py
+++ b/execution/backend/app/ai/prompts.py
@@ -9,7 +9,7 @@ from app.models.prompt_template import PromptTemplate
 
 PROMPT_KEYS = ("topics", "article_body", "article_label", "cover", "content_image",
                "builder_text", "category_seeds", "category_variants", "category_meta",
-               "category_seo_text")
+               "category_seo_text", "project_rewrite")
 
 # Набор переменных, который каждому промпту реально передаёт боевой код
 # (app/tasks.py и app/articles/builder.py). Объявлен здесь, а не разбросан по
@@ -42,6 +42,9 @@ PROMPT_VARIABLES: dict[str, frozenset[str]] = {
     "category_seo_text": frozenset({"site_name", "site_description", "category_name",
                                     "category_path", "form_nominative", "city_in", "h1",
                                     "keywords", "phrases", "products", "violations"}),
+    # Рерайт описания проекта дома: app/house_projects/rewriter.py.
+    "project_rewrite": frozenset({"site_name", "tone_of_voice", "title", "description_html",
+                                  "char_count", "violations"}),
 }
 
 # undefined=StrictUndefined: с дефолтным Undefined опечатка в имени переменной
````

`execution/backend/app/seed.py` — правка (дифф относительно main):

````diff
--- a/execution/backend/app/seed.py
+++ b/execution/backend/app/seed.py
@@ -340,6 +340,30 @@ city_prepositional — название города «{{ city }}» в пред
 {% endfor %}Объём держи тот же — около 2200 символов, примерно 300 слов: не длиннее и не короче.
 {% endif %}
 Верни только HTML текста — без пояснений, без JSON и без ```.""",
+
+    # Правила дублируют app/house_projects/validate.py — нарушения вернутся
+    # модели списком violations на второй попытке.
+    "project_rewrite": """Ты редактор сайта «{{ site_name }}». Перепиши своими словами описание
+проекта дома «{{ title }}»: тот же смысл, но другие слова, другое построение
+и порядок фраз. Этот же текст стоит на других сайтах — нужен уникальный вариант.
+{% if tone_of_voice %}
+Тон сайта: {{ tone_of_voice }}
+{% endif %}
+Требования:
+- объём — около {{ char_count }} символов видимого текста (без тегов): не длиннее и не короче;
+- все факты о проекте оставь как есть: числа, размеры, площади, этажность, материалы, конструкцию, отделку, состав помещений — ничего не меняй, не убирай и не добавляй; числа пиши цифрами, как в оригинале;
+- ничего нового: без города и региона, цен, сроков, гарантий, акций и обещаний, которых нет в оригинале;
+- разметка — как в оригинале: те же блоки в том же порядке, столько же подзаголовков <h3> и столько же пунктов в каждом списке; теги — только те, что есть в оригинале, без атрибутов и ссылок;
+- не копируй фразы оригинала — перестраивай предложения, а не только меняй слова местами; пиши живым языком, без штампов.
+
+Оригинал:
+{{ description_html }}
+{% if violations %}
+Предыдущий вариант не прошёл проверку. Исправь:
+{% for item in violations %}- {{ item }}
+{% endfor %}Объём держи около {{ char_count }} символов.
+{% endif %}
+Верни только HTML описания — без пояснений, без JSON и без ```.""",
 }
 
 
````

- [ ] **Step 4: Запустить — проходят**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_ai_prompts.py tests/test_api_admin_prompts.py tests/test_category_meta_prompts.py`
Expected: все зелёные

**Прод:** `seed_prompts` добавляет недостающий ключ и существующие не трогает — после деплоя промпт появится в БД сам (задача страницы зовёт `seed_prompts`).

- [ ] **Commit**

```bash
git add execution/backend/app/ai/prompts.py execution/backend/app/seed.py execution/backend/tests/test_ai_prompts.py execution/backend/tests/test_api_admin_prompts.py
git commit -m "feat: проекты домов — промпт рерайта описания

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 5: Клиент сайта — запись текста страницы как есть

`update_page_text` вырезает HTML-комментарии и обрезает пробелы, а на страницах проектов есть `<!-- Хар-ки и описание -->` — без `as_is` рерайт менял бы страницу вне блока.

**Files:**
- Modify: `execution/backend/app/sites/client.py`
- Modify: `execution/backend/tests/test_sites_client.py`

- [ ] **Step 1: Тест (красный)**

`execution/backend/tests/test_sites_client.py` — правка (дифф относительно main):

````diff
--- a/execution/backend/tests/test_sites_client.py
+++ b/execution/backend/tests/test_sites_client.py
@@ -163,6 +163,19 @@ def test_update_page_text_includes_title_and_meta_when_given(monkeypatch):
     }
 
 
+def test_update_page_text_as_is_keeps_comments_and_whitespace(monkeypatch):
+    captured = {}
+
+    def fake_patch(url, **kwargs):
+        captured.update(json=kwargs["json"])
+        return FakeResponse(200, {"id": 16})
+
+    monkeypatch.setattr("app.sites.client.requests.patch", fake_patch)
+    html = "<section>\n<!-- Хар-ки и описание -->\n<p>x</p>\n</section>\n"
+    SiteClient("https://x.ru", "token").update_page_text(16, html, as_is=True)
+    assert captured["json"] == {"text": html}
+
+
 def test_fetch_file_resolves_relative_url_against_base(monkeypatch):
     captured = {}
 
````

- [ ] **Step 2: Запустить — падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_sites_client.py -k as_is`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'as_is'`

- [ ] **Step 3: Реализация**

`execution/backend/app/sites/client.py` — правка (дифф относительно main):

````diff
--- a/execution/backend/app/sites/client.py
+++ b/execution/backend/app/sites/client.py
@@ -230,7 +230,7 @@ class SiteClient:
 
     def update_page_text(self, page_id: int, html: str, *, title: str | None = None,
                          meta_description: str | None = None,
-                         meta_keywords: str | None = None) -> dict:
+                         meta_keywords: str | None = None, as_is: bool = False) -> dict:
         """PATCH тела уже существующей страницы. Используется вне обычного
         потока сборки (build_for создаёт страницу один раз и больше не
         трогает) — для ручного исправления уже опубликованного контента
@@ -245,8 +245,13 @@ class SiteClient:
         менять только текст. regenerate_text — единственный вызывающий,
         которому нужны все четыре поля в одном PATCH: заголовок, meta и
         текст обновляются на сайте атомарно, а не рассинхронизированной
-        парой запросов."""
-        payload = {"text": strip_html_comments(html).strip()}
+        парой запросов.
+
+        as_is=True — HTML уходит без вырезания комментариев и обрезки: рерайт
+        проекта дома (app/house_projects/rewriter.py) меняет на странице один
+        блок, а всё вокруг, включая `<!-- Хар-ки и описание -->`, должно
+        остаться байт в байт."""
+        payload = {"text": html if as_is else strip_html_comments(html).strip()}
         if title is not None:
             payload["title"] = title
         if meta_description is not None:
````

- [ ] **Step 4: Запустить — проходит**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_sites_client.py`
Expected: все зелёные

- [ ] **Commit**

```bash
git add execution/backend/app/sites/client.py execution/backend/tests/test_sites_client.py
git commit -m "feat: клиент сайта — запись текста страницы без вырезания комментариев

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 6: Синхронизация страниц раздела

**Files:**
- Create: `execution/backend/app/house_projects/sync.py`
- Test: `execution/backend/tests/test_house_projects_sync.py`

- [ ] **Step 1: Тест (красный)**

`execution/backend/tests/test_house_projects_sync.py`:

````python
import pytest

from app.house_projects.sync import NOT_ON_SITE, sync_pages, with_retries
from app.models.house_project import HouseProjectPage
from app.models.site import Site
from app.sites.client import SiteAPIError


class FakeSite:
    def __init__(self, pages):
        self.pages = pages          # id → карточка
        self.reads = []

    def list_section_pages(self, prefix):
        assert prefix == ""
        return [{"id": pid, "title": p["title"], "url": p["url"]} for pid, p in self.pages.items()]

    def get_page(self, page_id):
        self.reads.append(page_id)
        return {"id": page_id, **self.pages[page_id]}


def card(title, url, parent):
    return {"title": title, "url": url, "parent": parent, "text": "<p>x</p>"}


@pytest.fixture
def site(db_session):
    row = Site(name="Стройбаза Самара", domain="stroybaza-samara.ru",
               base_url="https://stroybaza-samara.ru", api_token_enc="e",
               projects_enabled=True, projects_parent_id=15)
    db_session.add(row)
    db_session.commit()
    return row


def pages_of(db, site):
    return {p.remote_id: p for p in db.query(HouseProjectPage).filter_by(site_id=site.id).all()}


def test_only_direct_children_of_parent(db_session, site):
    client = FakeSite({
        15: card("Проекты домов", "/addresses/projects/", None),
        16: card("Проект дома из кирпича", "/pr/kirpich/", 15),
        25: card("Полезные статьи", "/blog/", None),
        76: card("Проект без родителя", "/pr/orphan/", None),
        80: card("Вложенный", "/pr/deep/", 16),
    })
    present = sync_pages(db_session, site, client)
    assert [p.remote_id for p in present] == [16]
    assert 15 not in client.reads          # родитель сам себе не ребёнок
    page = pages_of(db_session, site)[16]
    assert (page.status, page.title, page.url) == ("new", "Проект дома из кирпича", "/pr/kirpich/")


def test_existing_page_keeps_status_and_gets_new_title(db_session, site):
    db_session.add(HouseProjectPage(site_id=site.id, remote_id=16, title="старое",
                                    status="done", rewrite_count=1))
    db_session.commit()
    sync_pages(db_session, site, FakeSite({16: card("новое", "/pr/k/", 15)}))
    page = pages_of(db_session, site)[16]
    assert (page.status, page.title, page.rewrite_count) == ("done", "новое", 1)


def test_missing_page_is_skipped_and_restored_when_back(db_session, site):
    db_session.add(HouseProjectPage(site_id=site.id, remote_id=16, status="new"))
    db_session.commit()
    sync_pages(db_session, site, FakeSite({17: card("другой", "/pr/d/", 15)}))
    page = pages_of(db_session, site)[16]
    assert (page.status, page.skip_reason) == ("skipped", NOT_ON_SITE)

    sync_pages(db_session, site, FakeSite({16: card("вернулся", "/pr/k/", 15)}))
    assert (page.status, page.skip_reason) == ("new", "")


def test_moved_to_other_parent_is_skipped(db_session, site):
    db_session.add(HouseProjectPage(site_id=site.id, remote_id=16, status="done"))
    db_session.commit()
    sync_pages(db_session, site, FakeSite({16: card("переехал", "/x/", 99)}))
    assert pages_of(db_session, site)[16].skip_reason == NOT_ON_SITE


def test_skipped_without_description_is_rechecked(db_session, site):
    from app.clock import utcnow

    db_session.add(HouseProjectPage(site_id=site.id, remote_id=16, status="skipped",
                                    skip_reason="нет блока описания", rewritten_at=utcnow()))
    db_session.commit()
    sync_pages(db_session, site, FakeSite({16: card("т", "/pr/k/", 15)}))
    assert pages_of(db_session, site)[16].status == "done"


def test_with_retries_repeats_network_and_5xx_only():
    calls, pauses = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise SiteAPIError("сеть", status_code=None)
        return "ok"

    assert with_retries(flaky, sleep=pauses.append) == "ok"
    assert pauses == [1, 2]

    def not_found():
        raise SiteAPIError("нет", status_code=404)

    with pytest.raises(SiteAPIError):
        with_retries(not_found, sleep=pauses.append)
    assert pauses == [1, 2]


def test_with_retries_gives_up_after_three():
    pauses = []

    def down():
        raise SiteAPIError("502", status_code=502)

    with pytest.raises(SiteAPIError):
        with_retries(down, sleep=pauses.append)
    assert pauses == [1, 2]
````

- [ ] **Step 2: Запустить — падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_house_projects_sync.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.house_projects.sync'`

- [ ] **Step 3: Реализация**

`execution/backend/app/house_projects/sync.py`:

````python
"""Синхронизация страниц раздела «Проекты домов» с сайтом.

Список staticpages не отдаёт `parent`, фильтр `?parent=` сайт игнорирует, а url
детей (`/pr/...`) не совпадает с url родителя (`/addresses/projects/`) — поэтому
читается карточка каждой страницы сайта: на stroybaza-samara.ru 265 запросов,
1–2 минуты. Функция не коммитит: при сбое посреди чтения вызывающий откатывает
всё разом, и статусы страниц не меняются."""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models.house_project import HouseProjectPage
from app.sites.client import SiteAPIError

NOT_ON_SITE = "удалена на сайте"
MAX_ATTEMPTS = 3


def with_retries(call, sleep=time.sleep):
    """Сеть и 5xx сайта — до трёх попыток. Только для чтения и PATCH: их
    повторять безопасно, дублей они не создают."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            return call()
        except SiteAPIError as exc:
            retryable = exc.status_code is None or exc.status_code >= 500
            if not retryable or attempt == MAX_ATTEMPTS - 1:
                raise
            sleep(2 ** attempt)


def sync_pages(db: Session, site, site_client, sleep=time.sleep) -> list[HouseProjectPage]:
    """Прямые дети projects_parent_id — в панель; пропавшие — «удалена на сайте».
    Возвращает страницы, которые сейчас есть на сайте."""
    listed = with_retries(lambda: site_client.list_section_pages(""), sleep)
    children = []
    for item in listed:
        if item.get("id") == site.projects_parent_id:
            continue
        detail = with_retries(lambda: site_client.get_page(item["id"]), sleep)
        if detail.get("parent") == site.projects_parent_id:
            children.append(detail)

    existing = {row.remote_id: row for row in db.scalars(
        select(HouseProjectPage).where(HouseProjectPage.site_id == site.id)).all()}
    now = utcnow()
    present = []
    for detail in children:
        row = existing.get(detail["id"])
        if row is None:
            row = HouseProjectPage(site_id=site.id, remote_id=detail["id"], status="new",
                                   updated_at=now)
            db.add(row)
        row.title = (detail.get("title") or "")[:300]
        row.url = (detail.get("url") or "")[:500]
        if row.status == "skipped":
            # Вернулась на сайт или у неё появился блок описания — задача
            # страницы проверит блок заново.
            row.status = "done" if row.rewritten_at else "new"
            row.skip_reason = ""
            row.updated_at = now
        present.append(row)
    seen = {detail["id"] for detail in children}
    for remote_id, row in existing.items():
        if remote_id not in seen and row.skip_reason != NOT_ON_SITE:
            row.status, row.skip_reason, row.updated_at = "skipped", NOT_ON_SITE, now
    db.flush()
    return sorted(present, key=lambda row: row.remote_id)
````

- [ ] **Step 4: Запустить — проходит**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_house_projects_sync.py`
Expected: `7 passed`

- [ ] **Commit**

```bash
git add execution/backend/app/house_projects/sync.py execution/backend/tests/test_house_projects_sync.py
git commit -m "feat: проекты домов — синхронизация страниц раздела с сайтом

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 7: Состояние запусков

**Files:**
- Create: `execution/backend/app/house_projects/runs.py`
- Test: `execution/backend/tests/test_house_projects_runs.py`

- [ ] **Step 1: Тест (красный)**

`execution/backend/tests/test_house_projects_runs.py`:

````python
from datetime import timedelta

import pytest

from app.clock import utcnow
from app.house_projects.runs import (
    STALE_AFTER, STUCK_AFTER, abort_run, active_run, finish_run_if_complete, is_busy, is_lost,
    is_stale, is_stuck, next_queued, pages_for_mode, release_queued, run_counts,
)
from app.models.house_project import HouseProjectPage, ProjectRewriteRun
from app.models.job import JobRun
from app.models.site import Site


@pytest.fixture
def site(db_session):
    row = Site(name="С", domain="s.ru", base_url="https://s.ru", api_token_enc="e",
               projects_enabled=True, projects_parent_id=15)
    db_session.add(row)
    db_session.commit()
    return row


def add_page(db, site, remote_id, status="new", **fields):
    page = HouseProjectPage(site_id=site.id, remote_id=remote_id, status=status, **fields)
    db.add(page)
    db.commit()
    return page


def test_pages_for_mode(db_session, site):
    now = utcnow()
    add_page(db_session, site, 1, "new")
    add_page(db_session, site, 2, "done", rewritten_at=now)
    add_page(db_session, site, 3, "failed")
    add_page(db_session, site, 4, "skipped", skip_reason="удалена на сайте")
    add_page(db_session, site, 5, "in_work", started_at=now - STUCK_AFTER - timedelta(minutes=1))
    add_page(db_session, site, 6, "in_work", started_at=now)
    add_page(db_session, site, 7, "queued", updated_at=now - STALE_AFTER - timedelta(minutes=1))
    assert [p.remote_id for p in pages_for_mode(db_session, site.id, "new")] == [1, 3, 5, 7]
    assert [p.remote_id for p in pages_for_mode(db_session, site.id, "all")] == [1, 2, 3, 5, 6, 7]


def test_is_stuck_and_is_lost(db_session, site):
    now = utcnow()
    old = add_page(db_session, site, 1, "in_work", started_at=now - STUCK_AFTER - timedelta(seconds=1))
    fresh = add_page(db_session, site, 2, "in_work", started_at=now)
    assert is_stuck(old, now) and not is_stuck(fresh, now)
    lost = add_page(db_session, site, 3, "queued", updated_at=now - STALE_AFTER - timedelta(seconds=1))
    waiting = add_page(db_session, site, 4, "queued", updated_at=now)
    assert is_lost(lost, now) and not is_lost(waiting, now)


def test_run_counts_and_finish(db_session, site):
    run = ProjectRewriteRun(site_id=site.id, total=3, started_at=utcnow() - timedelta(minutes=5))
    db_session.add(run)
    db_session.commit()
    add_page(db_session, site, 1, "done")
    add_page(db_session, site, 2, "failed", updated_at=utcnow())
    add_page(db_session, site, 3, "queued", updated_at=utcnow())
    assert run_counts(db_session, run) == (1, 1)
    assert next_queued(db_session, site.id).remote_id == 3
    assert finish_run_if_complete(db_session, site.id) is None

    db_session.query(HouseProjectPage).filter_by(remote_id=3).one().status = "done"
    db_session.commit()
    assert finish_run_if_complete(db_session, site.id) is run
    assert run.finished_at is not None and active_run(db_session, site.id) is None


def test_old_failure_does_not_count_in_new_run(db_session, site):
    run = ProjectRewriteRun(site_id=site.id, total=1, started_at=utcnow())
    db_session.add(run)
    add_page(db_session, site, 1, "failed", updated_at=utcnow() - timedelta(days=1))
    assert run_counts(db_session, run) == (1, 0)


def test_stale_run(db_session, site):
    now = utcnow()
    run = ProjectRewriteRun(site_id=site.id, total=2, started_at=now - STALE_AFTER - timedelta(minutes=1))
    db_session.add(run)
    queued = add_page(db_session, site, 1, "queued", updated_at=now - STALE_AFTER - timedelta(minutes=1))
    assert is_stale(db_session, run, now)
    queued.updated_at = now
    db_session.commit()
    assert not is_stale(db_session, run, now)


def test_working_page_keeps_run_alive(db_session, site):
    now = utcnow()
    run = ProjectRewriteRun(site_id=site.id, total=1, started_at=now - STALE_AFTER * 2)
    db_session.add(run)
    add_page(db_session, site, 1, "in_work", started_at=now - timedelta(minutes=1),
             updated_at=now - STALE_AFTER * 2)
    assert not is_stale(db_session, run, now)


def test_is_busy(db_session, site):
    now = utcnow()
    assert not is_busy(db_session, site.id, now)
    page = add_page(db_session, site, 1, "queued", updated_at=now)
    assert is_busy(db_session, site.id, now)          # отдельная страница в очереди
    page.updated_at = now - STALE_AFTER - timedelta(minutes=1)
    db_session.commit()
    assert not is_busy(db_session, site.id, now)      # сообщение Celery потеряно
    run = ProjectRewriteRun(site_id=site.id, started_at=now)
    db_session.add(run)
    db_session.commit()
    assert is_busy(db_session, site.id, now)          # идёт синхронизация запуска


def test_release_and_abort(db_session, site):
    job = JobRun(kind="project_rewrite_run", status="running")
    db_session.add(job)
    db_session.commit()
    run = ProjectRewriteRun(site_id=site.id, total=2, job_run_id=job.id)
    db_session.add(run)
    db_session.commit()
    done_before = add_page(db_session, site, 1, "queued", rewritten_at=utcnow())
    never = add_page(db_session, site, 2, "queued")
    abort_run(db_session, run, "прерван")
    assert (done_before.status, never.status) == ("done", "new")
    assert run.error_text == "прерван" and run.finished_at is not None
    assert (job.status, job.log_text) == ("failed", "прерван")
    release_queued(db_session, site.id)    # повторно — без эффекта
    assert never.status == "new"
````

- [ ] **Step 2: Запустить — падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_house_projects_runs.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.house_projects.runs'`

- [ ] **Step 3: Реализация**

`execution/backend/app/house_projects/runs.py`:

````python
"""Состояние запуска «Запустить рерайт»: отбор страниц, прогресс, зависшие
страницы, оборванная цепочка задач."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import as_utc, utcnow
from app.models.house_project import HouseProjectPage, ProjectRewriteRun
from app.models.job import JobRun

ACTIVE_STATUSES = ("queued", "in_work")
# Мягкий лимит задачи страницы — 1200 с (PAGE_SOFT_LIMIT в app/tasks.py);
# раньше него «зависла» — ложная тревога.
STUCK_AFTER = timedelta(minutes=25)
# Цепочка может честно стоять в очереди Celery за партией статей — поэтому
# «оборвалась» только после двух часов без единого признака жизни.
STALE_AFTER = timedelta(hours=2)
MODES = ("new", "all")


@dataclass
class RunProgress:
    total: int
    done: int
    failed: int
    stale: bool


def is_stuck(page: HouseProjectPage, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return (page.status == "in_work" and page.started_at is not None
            and as_utc(page.started_at) < now - STUCK_AFTER)


def is_lost(page: HouseProjectPage, now: datetime | None = None) -> bool:
    """В очереди без признаков жизни два часа — сообщение Celery потеряно."""
    now = now or utcnow()
    return (page.status == "queued" and page.updated_at is not None
            and as_utc(page.updated_at) < now - STALE_AFTER)


def active_run(db: Session, site_id: int) -> ProjectRewriteRun | None:
    return db.scalars(select(ProjectRewriteRun).where(
        ProjectRewriteRun.site_id == site_id, ProjectRewriteRun.finished_at.is_(None))
        .order_by(ProjectRewriteRun.id.desc())).first()


def last_run(db: Session, site_id: int) -> ProjectRewriteRun | None:
    return db.scalars(select(ProjectRewriteRun).where(
        ProjectRewriteRun.site_id == site_id, ProjectRewriteRun.finished_at.is_not(None))
        .order_by(ProjectRewriteRun.finished_at.desc())).first()


def next_queued(db: Session, site_id: int) -> HouseProjectPage | None:
    return db.scalars(select(HouseProjectPage).where(
        HouseProjectPage.site_id == site_id, HouseProjectPage.status == "queued")
        .order_by(HouseProjectPage.remote_id)).first()


def active_pages(db: Session, site_id: int) -> list[HouseProjectPage]:
    return list(db.scalars(select(HouseProjectPage).where(
        HouseProjectPage.site_id == site_id,
        HouseProjectPage.status.in_(ACTIVE_STATUSES))).all())


def is_stale(db: Session, run: ProjectRewriteRun, now: datetime | None = None) -> bool:
    if run.finished_at is not None:
        return False
    now = now or utcnow()
    pages = active_pages(db, run.site_id)
    if any(page.status == "in_work" and not is_stuck(page, now) for page in pages):
        return False
    moments = [as_utc(run.started_at)] + [as_utc(p.updated_at) for p in pages if p.updated_at]
    return max(moments) < now - STALE_AFTER


def is_busy(db: Session, site_id: int, now: datetime | None = None) -> bool:
    """Идёт запуск или переписывается отдельная страница. Зависшая и потерянная
    страница проект не держат."""
    now = now or utcnow()
    run = active_run(db, site_id)
    if run is not None and not is_stale(db, run, now):
        return True
    return any(not is_stuck(page, now) and not is_lost(page, now)
               for page in active_pages(db, site_id))


def run_counts(db: Session, run: ProjectRewriteRun) -> tuple[int, int]:
    """(готово, ошибок) в пределах запуска: ошибкой считается страница, упавшая
    после его старта; «готово» — остаток (в том числе пропущенные без блока)."""
    started = as_utc(run.started_at)
    failed = sum(1 for page in db.scalars(select(HouseProjectPage).where(
                     HouseProjectPage.site_id == run.site_id,
                     HouseProjectPage.status == "failed"))
                 if page.updated_at is not None and as_utc(page.updated_at) >= started)
    active = len(active_pages(db, run.site_id))
    return max(run.total - active - failed, 0), failed


def run_progress(db: Session, run: ProjectRewriteRun, now: datetime | None = None) -> RunProgress:
    done, failed = run_counts(db, run)
    return RunProgress(total=run.total, done=done, failed=failed, stale=is_stale(db, run, now))


def pages_for_mode(db: Session, site_id: int, mode: str,
                   now: datetime | None = None) -> list[HouseProjectPage]:
    """new — ещё не переписанные, с ошибкой, зависшие и потерянные; all — все,
    кроме пропущенных."""
    rows = db.scalars(select(HouseProjectPage).where(
        HouseProjectPage.site_id == site_id, HouseProjectPage.status != "skipped")
        .order_by(HouseProjectPage.remote_id)).all()
    if mode == "all":
        return list(rows)
    return [row for row in rows
            if row.status in ("new", "failed") or is_stuck(row, now) or is_lost(row, now)]


def release_queued(db: Session, site_id: int, now: datetime | None = None) -> None:
    """Страницы оборванного запуска выходят из очереди в прежнее состояние."""
    now = now or utcnow()
    for page in db.scalars(select(HouseProjectPage).where(
            HouseProjectPage.site_id == site_id, HouseProjectPage.status == "queued")).all():
        page.status = "done" if page.rewritten_at else "new"
        page.updated_at = now


def abort_run(db: Session, run: ProjectRewriteRun, text: str) -> None:
    """Закрывает оборвавшийся запуск с пометкой и журнал его расходов."""
    now = utcnow()
    run.error_text, run.finished_at = text, now
    release_queued(db, run.site_id, now)
    job = db.get(JobRun, run.job_run_id) if run.job_run_id else None
    if job is not None and job.finished_at is None:
        job.status, job.log_text, job.finished_at = "failed", text, now
    db.commit()


def finish_run_if_complete(db: Session, site_id: int,
                           now: datetime | None = None) -> ProjectRewriteRun | None:
    """Закрывает открытый запуск, если у сайта не осталось queued/in_work."""
    run = active_run(db, site_id)
    if run is None or active_pages(db, site_id):
        return None
    run.finished_at = now or utcnow()
    db.commit()
    return run
````

- [ ] **Step 4: Запустить — проходит**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_house_projects_runs.py`
Expected: `8 passed`

- [ ] **Commit**

```bash
git add execution/backend/app/house_projects/runs.py execution/backend/tests/test_house_projects_runs.py
git commit -m "feat: проекты домов — отбор страниц, прогресс, зависшие и оборванные запуски

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 8: Рерайт одной страницы

**Files:**
- Create: `execution/backend/app/house_projects/rewriter.py`
- Test: `execution/backend/tests/test_house_projects_rewriter.py`

- [ ] **Step 1: Тест (красный)**

`execution/backend/tests/test_house_projects_rewriter.py`:

````python
from types import SimpleNamespace

import pytest

from app.ai.text import TextResult
from app.house_projects.markup import find_description, plain_text
from app.house_projects.rewriter import NO_DESCRIPTION, RewriteError, rewrite_page
from app.models.house_project import HouseProjectPage
from app.models.site import Site
from app.seed import seed_prompts

ORIGINAL = ("<p>Представляем проект уютного дома площадью 82.64 м², идеально подходящего "
            "для семьи из четырёх человек.</p>"
            "<h3>Ключевые преимущества:</h3>"
            "<ul><li>Монолитная плита под всем домом.</li>"
            "<li>Терраса 9.06 м² для отдыха.</li></ul>")
GOOD = ("<p>Перед вами комфортный дом на 82,64 м², который отлично подойдёт "
        "семье, где растут двое детей.</p>"
        "<h3>Почему выбирают проект:</h3>"
        "<ul><li>Под зданием залита монолитная плита.</li>"
        "<li>Для отдыха есть терраса 9.06 м².</li></ul>")
BAD = GOOD.replace("9.06", "девять")


def page_html(inner):
    return ('<section class="static-project">\n<!-- Хар-ки и описание -->\n'
            '<section class="project-chars"><ul class="char"><li>Площадь 82</li></ul></section>\n'
            '<section class="product-info__section project-description"><h2>Описание</h2>\n'
            f'<div class="amk-projects__about-1 product-info__text">\n{inner}\n</div>\n'
            '</section>\n{% custom_form "forma-rasschet" %}\n</section>')


class FakeSite:
    def __init__(self, inner):
        self.text = page_html(inner) if inner is not None else "<p>без блока</p>"
        self.patches = []

    def get_page(self, page_id):
        return {"id": page_id, "title": "Проект дома из кирпича", "url": "/pr/k/",
                "text": self.text}

    def update_page_text(self, page_id, html, *, as_is=False):
        assert as_is
        self.patches.append(html)
        self.text = html
        return {"id": page_id}


class FakeLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def complete_text(self, prompt, *, reasoning=True):
        assert reasoning is False
        self.prompts.append(prompt)
        return TextResult(self.answers.pop(0), 100, 50, 0.5)


@pytest.fixture
def site(db_session):
    seed_prompts(db_session)
    row = Site(name="Стройбаза", domain="s.ru", base_url="https://s.ru", api_token_enc="e",
               tone_of_voice="спокойный", projects_enabled=True, projects_parent_id=15)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def page(db_session, site):
    row = HouseProjectPage(site_id=site.id, remote_id=16, status="in_work")
    db_session.add(row)
    db_session.commit()
    return row


def run(db, page, site, client, llm, usage=None):
    rewrite_page(db, page, site, site_client=client, text_client=llm,
                 record_usage=lambda *a: (usage if usage is not None else []).append(a),
                 sleep=lambda s: None)


def test_first_rewrite_saves_original_and_writes_site(db_session, site, page):
    client, llm, usage = FakeSite(ORIGINAL), FakeLLM(GOOD), []
    before = client.text
    run(db_session, page, site, client, llm, usage)
    assert page.original_html == ORIGINAL
    assert (page.rewritten_html, page.status, page.rewrite_count) == (GOOD, "done", 1)
    assert page.rewritten_at is not None and page.title == "Проект дома из кирпича"
    block = find_description(before)
    assert client.patches[0] == before[:block.start] + "\n" + GOOD + "\n" + before[block.end:]
    assert usage == [(100, 50, 0.5)]
    assert "82.64 м²" in llm.prompts[0]
    assert f"около {len(plain_text(ORIGINAL))} символов" in llm.prompts[0]
    assert "Тон сайта: спокойный" in llm.prompts[0]


def test_repeat_rewrites_from_original(db_session, site, page):
    client = FakeSite(ORIGINAL)
    run(db_session, page, site, client, FakeLLM(GOOD))
    second = GOOD.replace("комфортный", "удобный")
    llm = FakeLLM(second)
    run(db_session, page, site, client, llm)
    assert page.original_html == ORIGINAL
    assert "Представляем проект" in llm.prompts[0]
    assert (page.rewritten_html, page.rewrite_count) == (second, 2)


def test_manual_edit_becomes_new_original(db_session, site, page):
    client = FakeSite(ORIGINAL)
    run(db_session, page, site, client, FakeLLM(GOOD))
    edited = ORIGINAL.replace("82.64", "84.10")
    client.text = page_html(edited)
    run(db_session, page, site, client, FakeLLM(GOOD.replace("82,64", "84,10")))
    assert page.original_html == edited


def test_whitespace_change_is_not_an_edit(db_session, site, page):
    client = FakeSite(ORIGINAL)
    run(db_session, page, site, client, FakeLLM(GOOD))
    client.text = page_html(GOOD.replace("</p>", "</p>\n   ").replace("</li>", "</li>\n"))
    run(db_session, page, site, client, FakeLLM(GOOD))
    assert page.original_html == ORIGINAL


def test_retry_with_violations_then_success(db_session, site, page):
    llm = FakeLLM(BAD, GOOD)
    run(db_session, page, site, FakeSite(ORIGINAL), llm)
    assert page.status == "done"
    assert "пропали числа из оригинала: 9.06" in llm.prompts[1]
    assert "Предыдущий вариант не прошёл проверку" not in llm.prompts[0]


def test_two_failures_raise_and_do_not_touch_site(db_session, site, page):
    client = FakeSite(ORIGINAL)
    with pytest.raises(RewriteError, match="пропали числа"):
        run(db_session, page, site, client, FakeLLM(BAD, BAD))
    assert client.patches == []
    assert page.original_html == ORIGINAL      # оригинал сохранён до вызова LLM


def test_page_without_description_is_skipped(db_session, site, page):
    client, llm = FakeSite(None), FakeLLM()
    run(db_session, page, site, client, llm)
    assert (page.status, page.skip_reason) == ("skipped", NO_DESCRIPTION)
    assert llm.prompts == [] and client.patches == []
````

- [ ] **Step 2: Запустить — падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_house_projects_rewriter.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.house_projects.rewriter'`

- [ ] **Step 3: Реализация**

`execution/backend/app/house_projects/rewriter.py`:

````python
"""Рерайт описания одной страницы проекта дома.

Порядок: свежая страница с сайта → оригинал (в первый раз — то, что на сайте;
правка руками после нас — новый оригинал) → LLM → проверка → вставка блока в
только что прочитанную страницу → PATCH. На сайт уходит только текст,
прошедший проверку."""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.ai.prompts import render_prompt, resolve_prompt
from app.clock import utcnow
from app.house_projects.markup import (
    clean_model_html, find_description, plain_text, replace_description, same_html,
)
from app.house_projects.sync import with_retries
from app.house_projects.validate import validate_rewrite
from app.models.house_project import HouseProjectPage

LLM_ATTEMPTS = 2     # первая попытка + одна с перечнем нарушений
NO_DESCRIPTION = "нет блока описания"


class RewriteError(RuntimeError):
    pass


def refresh_original(page: HouseProjectPage, current: str) -> None:
    """Повтор делается от оригинала. Но если описание на сайте не совпадает ни
    с оригиналом, ни с нашей последней версией, его поправили в админке — и
    правка становится новым оригиналом, иначе повтор затёр бы, например,
    исправленную площадь."""
    if not page.original_html:
        page.original_html = current
        return
    ours = bool(page.rewritten_html) and same_html(current, page.rewritten_html)
    if not ours and not same_html(current, page.original_html):
        page.original_html = current


def rewrite_page(db: Session, page: HouseProjectPage, site, *, site_client, text_client,
                 record_usage, sleep=time.sleep) -> None:
    detail = with_retries(lambda: site_client.get_page(page.remote_id), sleep)
    page_html = detail.get("text") or ""
    page.title = (detail.get("title") or page.title)[:300]
    page.url = (detail.get("url") or page.url)[:500]
    block = find_description(page_html)
    if block is None:
        page.status, page.skip_reason, page.error_text = "skipped", NO_DESCRIPTION, ""
        page.updated_at = utcnow()
        db.commit()
        return
    refresh_original(page, block.inner_html.strip())
    db.commit()

    template = resolve_prompt(db, "project_rewrite", site.id)
    violations: list[str] = []
    for _ in range(LLM_ATTEMPTS):
        result = text_client.complete_text(render_prompt(template, {
            "site_name": site.name, "tone_of_voice": site.tone_of_voice, "title": page.title,
            "description_html": page.original_html,
            "char_count": len(plain_text(page.original_html)), "violations": violations,
        }), reasoning=False)
        record_usage(result.tokens_prompt, result.tokens_completion, result.cost)
        candidate = clean_model_html(result.text)
        violations = validate_rewrite(page.original_html, candidate)
        if not violations:
            break
    else:
        raise RewriteError("рерайт не прошёл проверку: " + "; ".join(violations))

    new_html = replace_description(page_html, block, candidate)
    with_retries(lambda: site_client.update_page_text(page.remote_id, new_html, as_is=True),
                 sleep)
    now = utcnow()
    page.rewritten_html = candidate
    page.rewritten_at = now
    page.rewrite_count += 1
    page.status, page.error_text, page.skip_reason = "done", "", ""
    page.updated_at = now
    db.commit()
````

- [ ] **Step 4: Запустить — проходит**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_house_projects_rewriter.py`
Expected: `7 passed`

- [ ] **Commit**

```bash
git add execution/backend/app/house_projects/rewriter.py execution/backend/tests/test_house_projects_rewriter.py
git commit -m "feat: проекты домов — рерайт описания страницы от оригинала

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 9: Задачи Celery

**Files:**
- Modify: `execution/backend/app/tasks.py`
- Test: `execution/backend/tests/test_tasks_house_projects.py`

- [ ] **Step 1: Тест (красный)**

`execution/backend/tests/test_tasks_house_projects.py`:

````python
import pytest

from app.ai.factory import AIConfigError
from app.ai.text import LLMError, TextResult
from app.clock import utcnow
from app.models.house_project import HouseProjectPage, ProjectRewriteRun
from app.models.job import JobRun, LlmUsage
from app.models.site import Site
from app.sites.client import SiteAPIError
from app.tasks import rewrite_project_page_sync, start_rewrite_run_sync

ORIGINAL = ("<p>Представляем проект уютного дома площадью 82.64 м², идеально подходящего "
            "для семьи из четырёх человек.</p>"
            "<ul><li>Монолитная плита под всем домом.</li>"
            "<li>Терраса 9.06 м² для отдыха.</li></ul>")
GOOD = ("<p>Перед вами комфортный дом на 82,64 м², который отлично подойдёт "
        "семье, где растут двое детей.</p>"
        "<ul><li>Под зданием залита монолитная плита.</li>"
        "<li>Для отдыха есть терраса 9.06 м².</li></ul>")


def page_text(inner=ORIGINAL):
    return ('<section class="project-description"><div class="product-info__text">'
            f'{inner}</div></section>')


class FakeSite:
    def __init__(self, children, list_error=None):
        self.children = children          # remote_id → текст страницы
        self.list_error = list_error
        self.patches = []

    def list_section_pages(self, prefix):
        if self.list_error:
            raise self.list_error
        return [{"id": 15}] + [{"id": pid} for pid in self.children]

    def get_page(self, page_id):
        if page_id == 15:
            return {"id": 15, "parent": None, "title": "Проекты домов", "text": ""}
        return {"id": page_id, "parent": 15, "title": f"Проект {page_id}",
                "url": f"/pr/{page_id}/", "text": self.children[page_id]}

    def update_page_text(self, page_id, html, *, as_is=False):
        self.patches.append(page_id)
        self.children[page_id] = html
        return {"id": page_id}


class FakeLLM:
    model = "m"

    def __init__(self, answer=GOOD, error=None):
        self.answer, self.error = answer, error

    def complete_text(self, prompt, *, reasoning=True):
        if self.error:
            raise self.error
        return TextResult(self.answer, 10, 5, 0.2)


@pytest.fixture
def site(db_session):
    from app.seed import seed_prompts

    seed_prompts(db_session)
    row = Site(name="Стройбаза", domain="s.ru", base_url="https://s.ru", api_token_enc="e",
               projects_enabled=True, projects_parent_id=15)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def patch(monkeypatch):
    def apply(site_client, llm=None):
        monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: site_client)
        if isinstance(llm, Exception):
            def broken(db):
                raise llm
            monkeypatch.setattr("app.tasks.build_text_client", broken)
        else:
            monkeypatch.setattr("app.tasks.build_text_client", lambda db: llm or FakeLLM())
        monkeypatch.setattr("app.house_projects.sync.time.sleep", lambda s: None)
    return apply


def new_run(db, site, mode="new"):
    run = ProjectRewriteRun(site_id=site.id, mode=mode, started_at=utcnow())
    db.add(run)
    db.commit()
    return run


def pages(db, site):
    return {p.remote_id: p for p in db.query(HouseProjectPage).filter_by(site_id=site.id).all()}


def test_start_syncs_and_queues_new_pages(db_session, site, patch):
    patch(FakeSite({16: page_text(), 17: page_text()}))
    run = new_run(db_session, site)
    first = start_rewrite_run_sync(db_session, run.id)
    rows = pages(db_session, site)
    assert first == rows[16].id
    assert {r.status for r in rows.values()} == {"queued"}
    assert run.total == 2
    assert db_session.get(JobRun, run.job_run_id).kind == "project_rewrite_run"


def test_mode_new_skips_done_and_all_takes_them(db_session, site, patch):
    db_session.add(HouseProjectPage(site_id=site.id, remote_id=16, status="done",
                                    rewritten_at=utcnow()))
    db_session.commit()
    patch(FakeSite({16: page_text(), 17: page_text()}))
    run = new_run(db_session, site, "new")
    start_rewrite_run_sync(db_session, run.id)
    assert (run.total, pages(db_session, site)[16].status) == (1, "done")

    for row in pages(db_session, site).values():
        row.status = "done"
    run.finished_at = utcnow()
    db_session.commit()
    second = new_run(db_session, site, "all")
    start_rewrite_run_sync(db_session, second.id)
    assert second.total == 2


def test_nothing_to_do_closes_run(db_session, site, patch):
    patch(FakeSite({}))
    run = new_run(db_session, site)
    assert start_rewrite_run_sync(db_session, run.id) is None
    assert run.finished_at is not None and run.error_text == ""
    assert db_session.get(JobRun, run.job_run_id).status == "ok"


def test_sync_failure_fails_run_and_keeps_statuses(db_session, site, patch):
    db_session.add(HouseProjectPage(site_id=site.id, remote_id=16, status="new"))
    db_session.commit()
    patch(FakeSite({}, list_error=SiteAPIError("список страниц: HTTP 401", status_code=401)))
    run = new_run(db_session, site)
    assert start_rewrite_run_sync(db_session, run.id) is None
    assert "HTTP 401" in run.error_text and run.finished_at is not None
    assert pages(db_session, site)[16].status == "new"


def test_chain_rewrites_pages_and_finishes_run(db_session, site, patch):
    client = FakeSite({16: page_text(), 17: page_text()})
    patch(client)
    run = new_run(db_session, site)
    first = start_rewrite_run_sync(db_session, run.id)
    following = rewrite_project_page_sync(db_session, first)
    assert following == pages(db_session, site)[17].id
    assert rewrite_project_page_sync(db_session, following) is None
    rows = pages(db_session, site)
    assert {r.status for r in rows.values()} == {"done"}
    assert client.patches == [16, 17]
    assert run.finished_at is not None
    job = db_session.get(JobRun, run.job_run_id)
    assert (job.status, job.log_text) == ("ok", "готово 2/2, ошибок 0")
    assert db_session.query(LlmUsage).filter_by(job_run_id=job.id).count() == 2


def test_llm_failure_fails_page_and_chain_goes_on(db_session, site, patch):
    patch(FakeSite({16: page_text(), 17: page_text()}),
          FakeLLM(error=LLMError("LLM недоступна после 3 попыток")))
    run = new_run(db_session, site)
    first = start_rewrite_run_sync(db_session, run.id)
    following = rewrite_project_page_sync(db_session, first)
    page = pages(db_session, site)[16]
    assert (page.status, page.error_text) == ("failed", "LLM недоступна после 3 попыток")
    assert following == pages(db_session, site)[17].id


def test_config_error_fails_whole_queue(db_session, site, patch):
    patch(FakeSite({16: page_text(), 17: page_text()}),
          AIConfigError("ключ RouterAI не задан"))
    run = new_run(db_session, site)
    first = start_rewrite_run_sync(db_session, run.id)
    assert rewrite_project_page_sync(db_session, first) is None
    assert {r.status for r in pages(db_session, site).values()} == {"failed"}
    assert run.finished_at is not None
    assert db_session.get(JobRun, run.job_run_id).status == "failed"


def test_single_page_rewrite_has_own_job_and_no_chain(db_session, site, patch):
    client = FakeSite({16: page_text(), 17: page_text()})
    patch(client)
    a = HouseProjectPage(site_id=site.id, remote_id=16, status="queued")
    b = HouseProjectPage(site_id=site.id, remote_id=17, status="queued")
    db_session.add_all([a, b])
    db_session.commit()
    assert rewrite_project_page_sync(db_session, a.id, continue_run=False) is None
    assert (a.status, b.status) == ("done", "queued")
    job = db_session.query(JobRun).filter_by(kind="project_rewrite_page").one()
    assert job.status == "ok"


def test_page_taken_by_other_task_is_not_processed_twice(db_session, site, patch):
    client = FakeSite({16: page_text()})
    patch(client)
    page = HouseProjectPage(site_id=site.id, remote_id=16, status="done")
    db_session.add(page)
    db_session.commit()
    assert rewrite_project_page_sync(db_session, page.id) is None
    assert client.patches == []
````

- [ ] **Step 2: Запустить — падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_tasks_house_projects.py`
Expected: FAIL — `ImportError: cannot import name 'rewrite_project_page_sync' from 'app.tasks'`

- [ ] **Step 3: Реализация**

`execution/backend/app/tasks.py` — правка (дифф относительно main):

````diff
--- a/execution/backend/app/tasks.py
+++ b/execution/backend/app/tasks.py
@@ -27,9 +27,13 @@ from app.celery_app import celery_app
 from app.clock import utcnow
 from app.companies.builder import build_for as build_for_company
 from app.db import SessionLocal
+from app.house_projects import runs as rewrite_runs
+from app.house_projects.rewriter import RewriteError, rewrite_page
+from app.house_projects.sync import sync_pages
 from app.models.article import Article, ArticleBatch
 from app.models.company import Company, CompanyBatch
 from app.models.category_meta import CategoryMeta, MetaRun
+from app.models.house_project import HouseProjectPage, ProjectRewriteRun
 from app.models.job import JobRun, LlmUsage
 from app.models.site import Site
 from app.seed import seed_prompts
@@ -891,3 +895,188 @@ def generate_category_meta(category_id: int, continue_run: bool = True) -> None:
         enqueue_category_meta(category_id, continue_run=continue_run, countdown=countdown)
     elif following is not None:
         enqueue_category_meta(following)
+
+
+# --- проекты домов: рерайт описаний (directions/2026-10-18-house-projects-rewrite-design.md) ---
+
+# Худший случай страницы: чтение 3 × 60 с + паузы 1+2 = 183 с; LLM 2 попытки ×
+# 366 с = 732 с; PATCH 3 × 60 с + 3 = 183 с. Итого ≈ 1100 с; обычно — минута.
+PAGE_SOFT_LIMIT = 1200
+PAGE_HARD_LIMIT = 1260
+# Синхронизация — карточка каждой страницы сайта: на stroybaza-samara.ru 265
+# запросов, 1–2 минуты; запас на медленный сайт и повторы.
+REWRITE_START_SOFT_LIMIT = 1800
+REWRITE_START_HARD_LIMIT = 1860
+
+
+def _close_rewrite_job(db, run: ProjectRewriteRun) -> None:
+    job = db.get(JobRun, run.job_run_id) if run.job_run_id else None
+    if job is None or job.finished_at is not None:
+        return
+    if run.error_text:
+        _finish_job(db, job, "failed", run.error_text)
+        return
+    done, failed = rewrite_runs.run_counts(db, run)
+    _finish_job(db, job, "ok" if not failed else "failed",
+                f"готово {done}/{run.total}, ошибок {failed}")
+
+
+def _complete_rewrite_run(db, site_id: int) -> None:
+    run = rewrite_runs.finish_run_if_complete(db, site_id)
+    if run is not None:
+        _close_rewrite_job(db, run)
+
+
+def _fail_rewrite_run(db, run: ProjectRewriteRun, text: str) -> None:
+    run.error_text = text
+    run.finished_at = utcnow()
+    db.commit()
+    _close_rewrite_job(db, run)
+
+
+def start_rewrite_run_sync(db, run_id: int) -> int | None:
+    """Синхронизация страниц и отбор по режиму; возвращает id первой страницы цепочки."""
+    run = db.get(ProjectRewriteRun, run_id)
+    site = db.get(Site, run.site_id)
+    job = _start_job(db, "project_rewrite_run", site.id, run.created_by_id,
+                     {"run_id": run_id, "mode": run.mode})
+    run.job_run_id = job.id
+    db.commit()
+    try:
+        sync_pages(db, site, open_site_client(db, site))
+        rows = rewrite_runs.pages_for_mode(db, site.id, run.mode)
+        now = utcnow()
+        for row in rows:
+            row.status, row.error_text, row.started_at, row.updated_at = "queued", "", None, now
+        run.total = len(rows)
+        db.commit()
+    except SoftTimeLimitExceeded:
+        db.rollback()
+        _fail_rewrite_run(db, run, "превышен лимит времени синхронизации страниц")
+        return None
+    except (SiteAPIError, SecretDecryptionError) as exc:
+        db.rollback()
+        _fail_rewrite_run(db, run, str(exc))
+        return None
+    except Exception as exc:  # noqa: BLE001 — барьер, см. run_batch_sync
+        db.rollback()
+        _fail_rewrite_run(db, run, f"непредвиденная ошибка: {type(exc).__name__}: {exc}")
+        raise
+
+    first = rewrite_runs.next_queued(db, site.id)
+    if first is None:
+        _complete_rewrite_run(db, site.id)
+        return None
+    return first.id
+
+
+def _fail_page(db, page: HouseProjectPage, text: str) -> None:
+    page.status = "failed"
+    page.error_text = text
+    page.updated_at = utcnow()
+    db.commit()
+
+
+def _fail_queued_pages(db, site_id: int, text: str) -> None:
+    """Ошибка конфигурации (ключ RouterAI, токен сайта) — одна на весь запуск."""
+    for row in db.query(HouseProjectPage).filter(HouseProjectPage.site_id == site_id,
+                                                 HouseProjectPage.status == "queued").all():
+        row.status, row.error_text, row.updated_at = "failed", text, utcnow()
+    db.commit()
+
+
+def rewrite_project_page_sync(db, page_id: int, continue_run: bool = True) -> int | None:
+    """Рерайт одной страницы; возвращает id следующей страницы цепочки."""
+    page = db.get(HouseProjectPage, page_id)
+    if page is None:
+        return None
+    site = db.get(Site, page.site_id)
+    if page.status != "queued":
+        # Страницу уже взяла другая задача — не обрабатываем дважды, цепочку не рвём.
+        if not continue_run:
+            return None
+        following = rewrite_runs.next_queued(db, site.id)
+        return following.id if following and following.id != page_id else None
+
+    run = rewrite_runs.active_run(db, site.id) if continue_run else None
+    own_job: JobRun | None = None
+
+    def job_id() -> int:
+        nonlocal own_job
+        if run is not None and run.job_run_id is not None:
+            return run.job_run_id
+        if own_job is None:
+            own_job = _start_job(db, "project_rewrite_page", site.id, None, {"page_id": page_id})
+        return own_job.id
+
+    page.status = "in_work"
+    page.started_at = utcnow()
+    page.updated_at = utcnow()
+    db.commit()
+
+    stop_run_text = ""
+    try:
+        seed_prompts(db)   # промпт project_rewrite появился после первых деплоев
+        text_client = build_text_client(db)
+        rewrite_page(db, page, site, site_client=open_site_client(db, site),
+                     text_client=text_client,
+                     record_usage=lambda tp, tc, cost: _record_usage(
+                         db, job_id(), text_client.model, tp, tc, cost))
+    except SoftTimeLimitExceeded:
+        db.rollback()
+        _fail_page(db, page, "превышен лимит времени задачи")
+    except (AIConfigError, SecretDecryptionError) as exc:
+        db.rollback()
+        _fail_page(db, page, str(exc))
+        stop_run_text = str(exc)
+    except (LLMError, PromptError, SiteAPIError, RewriteError) as exc:
+        db.rollback()
+        _fail_page(db, page, str(exc))
+    except Exception as exc:  # noqa: BLE001 — барьер: цепочка не должна рваться
+        logger.exception("рерайт страницы проекта %s: непредвиденная ошибка", page_id)
+        db.rollback()
+        _fail_page(db, page, f"непредвиденная ошибка: {type(exc).__name__}: {exc}")
+
+    if own_job is not None:
+        _finish_job(db, own_job, "ok" if page.status in ("done", "skipped") else "failed",
+                    page.error_text)
+    if stop_run_text:
+        _fail_queued_pages(db, site.id, stop_run_text)
+    _complete_rewrite_run(db, site.id)
+    if not continue_run:
+        return None
+    following = rewrite_runs.next_queued(db, site.id)
+    return following.id if following else None
+
+
+def enqueue_rewrite_page(page_id: int, *, continue_run: bool = True) -> None:
+    rewrite_project_page.apply_async(
+        args=[page_id], kwargs={"continue_run": continue_run},
+        soft_time_limit=PAGE_SOFT_LIMIT, time_limit=PAGE_HARD_LIMIT)
+
+
+def enqueue_rewrite_run(run_id: int) -> None:
+    start_rewrite_run.apply_async(args=[run_id], soft_time_limit=REWRITE_START_SOFT_LIMIT,
+                                  time_limit=REWRITE_START_HARD_LIMIT)
+
+
+@celery_app.task(name="app.tasks.start_rewrite_run")
+def start_rewrite_run(run_id: int) -> None:
+    db = SessionLocal()
+    try:
+        first = start_rewrite_run_sync(db, run_id)
+    finally:
+        db.close()
+    if first is not None:
+        enqueue_rewrite_page(first)
+
+
+@celery_app.task(name="app.tasks.rewrite_project_page")
+def rewrite_project_page(page_id: int, continue_run: bool = True) -> None:
+    db = SessionLocal()
+    try:
+        following = rewrite_project_page_sync(db, page_id, continue_run)
+    finally:
+        db.close()
+    if following is not None:
+        enqueue_rewrite_page(following)
````

- [ ] **Step 4: Запустить — проходит**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_tasks_house_projects.py tests/test_tasks.py tests/test_tasks_category_meta.py`
Expected: все зелёные (`9 passed` в новом файле)

- [ ] **Commit**

```bash
git add execution/backend/app/tasks.py execution/backend/tests/test_tasks_house_projects.py
git commit -m "feat: проекты домов — запуск рерайта цепочкой задач по страницам

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 10: API раздела

**Files:**
- Create: `execution/backend/app/api/house_projects.py`
- Modify: `execution/backend/app/main.py`
- Test: `execution/backend/tests/test_api_house_projects.py`

- [ ] **Step 1: Тест (красный)**

`execution/backend/tests/test_api_house_projects.py`:

````python
from datetime import timedelta

import pytest

from app.clock import utcnow
from app.house_projects.runs import STALE_AFTER, STUCK_AFTER
from app.models.house_project import HouseProjectPage, ProjectRewriteRun
from app.models.site import Site
from app.sites.client import SiteAPIError


@pytest.fixture
def site(db_session):
    row = Site(name="Стройбаза Самара", domain="stroybaza-samara.ru",
               base_url="https://stroybaza-samara.ru", api_token_enc="e")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def project(db_session, site):
    site.projects_enabled, site.projects_parent_id = True, 15
    site.projects_parent_title = "Проекты домов"
    db_session.commit()
    return site


class FakeSite:
    def __init__(self, error=None):
        self.error = error

    def get_page(self, page_id):
        if self.error:
            raise self.error
        return {"id": page_id, "title": "Проекты домов", "parent": None}


@pytest.fixture
def site_client(monkeypatch):
    client = FakeSite()
    monkeypatch.setattr("app.api.house_projects.open_site_client", lambda db, site: client)
    return client


@pytest.fixture
def enqueued(monkeypatch):
    calls = {"runs": [], "pages": []}
    monkeypatch.setattr("app.api.house_projects.enqueue_rewrite_run",
                        lambda run_id: calls["runs"].append(run_id))
    monkeypatch.setattr("app.api.house_projects.enqueue_rewrite_page",
                        lambda page_id, **kw: calls["pages"].append((page_id, kw)))
    return calls


def add_page(db, site, remote_id, status="new", **fields):
    page = HouseProjectPage(site_id=site.id, remote_id=remote_id, status=status,
                            title=f"Проект {remote_id}", url=f"/pr/{remote_id}/", **fields)
    db.add(page)
    db.commit()
    return page


def test_requires_login(client):
    assert client.get("/api/house-projects/projects").status_code == 401


def test_create_project_checks_parent_and_shows_title(manager_client, site, site_client):
    resp = manager_client.post("/api/house-projects/projects",
                               json={"site_id": site.id, "parent_id": 15})
    assert resp.status_code == 200
    body = resp.json()
    assert (body["parent_id"], body["parent_title"], body["pages"], body["run"]) == \
        (15, "Проекты домов", 0, None)
    assert [p["site_id"] for p in manager_client.get("/api/house-projects/projects").json()] \
        == [site.id]


def test_create_project_unknown_parent(manager_client, site, site_client):
    site_client.error = SiteAPIError("страница 999: HTTP 404", status_code=404)
    resp = manager_client.post("/api/house-projects/projects",
                               json={"site_id": site.id, "parent_id": 999})
    assert (resp.status_code, resp.json()["detail"]) == (400, "страница 999 не найдена на сайте")
    assert not site.projects_enabled


def test_create_project_site_down(manager_client, site, site_client):
    site_client.error = SiteAPIError("сеть недоступна", status_code=None)
    resp = manager_client.post("/api/house-projects/projects",
                               json={"site_id": site.id, "parent_id": 15})
    assert resp.status_code == 502


def test_create_twice_and_unknown_site(manager_client, project, site_client):
    assert manager_client.post("/api/house-projects/projects",
                               json={"site_id": project.id, "parent_id": 15}).status_code == 400
    assert manager_client.post("/api/house-projects/projects",
                               json={"site_id": 999, "parent_id": 15}).status_code == 404


def test_project_counts(manager_client, db_session, project):
    now = utcnow()
    add_page(db_session, project, 16, "done", rewritten_at=now - timedelta(days=1))
    add_page(db_session, project, 17, "done", rewritten_at=now)
    add_page(db_session, project, 18, "new")
    add_page(db_session, project, 19, "failed", error_text="x")
    add_page(db_session, project, 20, "skipped", skip_reason="удалена на сайте")
    body = manager_client.get("/api/house-projects/projects").json()[0]
    assert (body["pages"], body["rewritten"], body["new_count"], body["error_count"],
            body["busy"]) == (4, 2, 2, 1, False)
    assert body["last_rewritten_at"].startswith(now.strftime("%Y-%m-%dT%H:%M"))


def test_run_starts_and_second_is_409(manager_client, db_session, project, enqueued):
    resp = manager_client.post(f"/api/house-projects/projects/{project.id}/run",
                               json={"mode": "all"})
    assert resp.status_code == 200
    run = db_session.query(ProjectRewriteRun).one()
    assert (run.mode, enqueued["runs"]) == ("all", [run.id])
    assert resp.json()["run"]["total"] == 0 and resp.json()["busy"]
    again = manager_client.post(f"/api/house-projects/projects/{project.id}/run",
                                json={"mode": "new"})
    assert again.status_code == 409


def test_run_rejects_unknown_mode(manager_client, project, enqueued):
    resp = manager_client.post(f"/api/house-projects/projects/{project.id}/run",
                               json={"mode": "everything"})
    assert resp.status_code == 422


def test_stale_run_is_aborted_and_restarted(manager_client, db_session, project, enqueued):
    long_ago = utcnow() - STALE_AFTER - timedelta(minutes=5)
    old = ProjectRewriteRun(site_id=project.id, total=1, started_at=long_ago)
    db_session.add(old)
    db_session.commit()
    page = add_page(db_session, project, 16, "queued", updated_at=long_ago)
    resp = manager_client.post(f"/api/house-projects/projects/{project.id}/run",
                               json={"mode": "new"})
    assert resp.status_code == 200
    assert old.finished_at is not None and old.error_text.startswith("прерван")
    assert page.status == "new"


def test_single_page_blocks_run(manager_client, db_session, project, enqueued):
    add_page(db_session, project, 16, "in_work", started_at=utcnow(), updated_at=utcnow())
    resp = manager_client.post(f"/api/house-projects/projects/{project.id}/run",
                               json={"mode": "new"})
    assert resp.status_code == 409


def test_list_and_detail_pages(manager_client, db_session, project):
    page = add_page(db_session, project, 16, "done", original_html="<p>Дом 82 м²</p>",
                    rewritten_html="<p>Дом на 82 м²</p>", rewritten_at=utcnow(), rewrite_count=2)
    rows = manager_client.get(f"/api/house-projects/projects/{project.id}/pages").json()
    assert rows[0]["page_url"] == "https://stroybaza-samara.ru/pr/16/"
    assert (rows[0]["original_chars"], rows[0]["rewritten_chars"], rows[0]["rewrite_count"]) == \
        (9, 12, 2)
    assert "original_html" not in rows[0]
    detail = manager_client.get(f"/api/house-projects/pages/{page.id}").json()
    assert detail["rewritten_html"] == "<p>Дом на 82 м²</p>"


def test_rewrite_one(manager_client, db_session, project, enqueued):
    page = add_page(db_session, project, 16, "done", error_text="")
    resp = manager_client.post(f"/api/house-projects/pages/{page.id}/rewrite")
    assert resp.status_code == 200
    assert page.status == "queued"
    assert enqueued["pages"] == [(page.id, {"continue_run": False})]
    assert manager_client.post(f"/api/house-projects/pages/{page.id}/rewrite").status_code == 409


def test_rewrite_one_refused_while_run_or_skipped(manager_client, db_session, project, enqueued):
    skipped = add_page(db_session, project, 16, "skipped", skip_reason="нет блока описания")
    resp = manager_client.post(f"/api/house-projects/pages/{skipped.id}/rewrite")
    assert (resp.status_code, resp.json()["detail"]) == \
        (400, "страница пропущена: нет блока описания")
    page = add_page(db_session, project, 17, "done")
    db_session.add(ProjectRewriteRun(site_id=project.id, started_at=utcnow()))
    db_session.commit()
    assert manager_client.post(f"/api/house-projects/pages/{page.id}/rewrite").status_code == 409


def test_rewrite_one_allowed_for_stuck_and_lost(manager_client, db_session, project, enqueued):
    stuck = add_page(db_session, project, 16, "in_work",
                     started_at=utcnow() - STUCK_AFTER - timedelta(minutes=1))
    lost = add_page(db_session, project, 17, "queued",
                    updated_at=utcnow() - STALE_AFTER - timedelta(minutes=1))
    rows = manager_client.get(f"/api/house-projects/projects/{project.id}/pages").json()
    assert [row["stuck"] for row in rows] == [True, True]
    assert manager_client.get("/api/house-projects/projects").json()[0]["error_count"] == 2
    for page in (stuck, lost):
        assert manager_client.post(f"/api/house-projects/pages/{page.id}/rewrite").status_code == 200


def test_update_and_delete_project(manager_client, db_session, project, site_client, enqueued):
    resp = manager_client.put(f"/api/house-projects/projects/{project.id}",
                              json={"site_id": project.id, "parent_id": 40})
    assert (resp.status_code, resp.json()["parent_id"]) == (200, 40)
    db_session.add(ProjectRewriteRun(site_id=project.id, started_at=utcnow()))
    db_session.commit()
    assert manager_client.delete(f"/api/house-projects/projects/{project.id}").status_code == 409
    run = db_session.query(ProjectRewriteRun).one()
    run.finished_at = utcnow()
    db_session.commit()
    assert manager_client.delete(f"/api/house-projects/projects/{project.id}").status_code == 200
    assert manager_client.get("/api/house-projects/projects").json() == []
````

- [ ] **Step 2: Запустить — падает**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_api_house_projects.py`
Expected: FAIL — `3 failed, 12 errors`: у тестов с подменой `ModuleNotFoundError: No module named 'app.api.house_projects'`, у остальных 404 вместо ответов раздела (`assert 404 == 401`)

- [ ] **Step 3: Реализация**

`execution/backend/app/api/house_projects.py`:

````python
"""API раздела «Проекты домов» (directions/2026-10-18-house-projects-rewrite-design.md).

Проект — карточка сайта с projects_enabled=True; отдельной сущности нет.
Доступ — всем залогиненным, как у «Метатегов категорий»."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin_sites import open_client as open_site_client
from app.api.deps import get_current_user, get_db
from app.clock import utcnow
from app.house_projects.markup import plain_text
from app.house_projects.runs import (
    abort_run, active_run, is_busy, is_lost, is_stale, is_stuck, last_run, pages_for_mode,
    run_progress,
)
from app.models.house_project import HouseProjectPage, ProjectRewriteRun
from app.models.site import Site
from app.models.user import User
from app.settings.crypto import SecretDecryptionError
from app.sites.client import SiteAPIError
from app.tasks import enqueue_rewrite_page, enqueue_rewrite_run

router = APIRouter(prefix="/api/house-projects", tags=["house-projects"])

BUSY_TEXT = "идёт рерайт проекта — дождитесь окончания"


class ProjectIn(BaseModel):
    site_id: int
    parent_id: int


class RunIn(BaseModel):
    mode: Literal["new", "all"]


class RunOut(BaseModel):
    id: int
    mode: str
    total: int
    done: int
    failed: int
    started_at: datetime
    stale: bool


class ProjectOut(BaseModel):
    site_id: int
    name: str
    domain: str
    parent_id: int
    parent_title: str
    pages: int                       # страниц раздела на сайте (без пропавших)
    rewritten: int                   # из них переписаны хотя бы раз
    new_count: int                   # возьмёт режим «Только новые»
    error_count: int                 # с ошибкой или зависшие
    last_rewritten_at: datetime | None
    last_error: str                  # ошибка последнего завершённого запуска
    busy: bool                       # запуск или отдельная страница в работе
    run: RunOut | None


class PageOut(BaseModel):
    id: int
    remote_id: int
    title: str
    url: str
    page_url: str
    status: str
    skip_reason: str
    error_text: str
    stuck: bool                      # зависла в работе или потерялась в очереди
    rewritten_at: datetime | None
    rewrite_count: int
    original_chars: int
    rewritten_chars: int
    updated_at: datetime


class PageDetailOut(PageOut):
    original_html: str
    rewritten_html: str


def _project_out(db: Session, site: Site) -> ProjectOut:
    run = active_run(db, site.id)
    run_out = None
    if run is not None:
        progress = run_progress(db, run)
        run_out = RunOut(id=run.id, mode=run.mode, total=progress.total, done=progress.done,
                         failed=progress.failed, started_at=run.started_at,
                         stale=progress.stale)
    rows = db.scalars(select(HouseProjectPage).where(
        HouseProjectPage.site_id == site.id, HouseProjectPage.status != "skipped")).all()
    dates = [row.rewritten_at for row in rows if row.rewritten_at is not None]
    finished = last_run(db, site.id)
    return ProjectOut(
        site_id=site.id, name=site.name, domain=site.domain,
        parent_id=site.projects_parent_id, parent_title=site.projects_parent_title,
        pages=len(rows), rewritten=len(dates),
        new_count=len(pages_for_mode(db, site.id, "new")),
        error_count=sum(1 for row in rows
                        if row.status == "failed" or is_stuck(row) or is_lost(row)),
        last_rewritten_at=max(dates) if dates else None,
        last_error=finished.error_text if finished else "",
        busy=is_busy(db, site.id), run=run_out)


def _page_out(site: Site, row: HouseProjectPage) -> dict:
    return dict(
        id=row.id, remote_id=row.remote_id, title=row.title, url=row.url,
        page_url=f"{site.base_url.rstrip('/')}{row.url}" if row.url else "",
        status=row.status, skip_reason=row.skip_reason, error_text=row.error_text,
        stuck=is_stuck(row) or is_lost(row), rewritten_at=row.rewritten_at,
        rewrite_count=row.rewrite_count,
        original_chars=len(plain_text(row.original_html)),
        rewritten_chars=len(plain_text(row.rewritten_html)), updated_at=row.updated_at)


def _project_or_404(db: Session, site_id: int) -> Site:
    site = db.get(Site, site_id)
    if site is None or not site.projects_enabled:
        raise HTTPException(404, "проект не найден")
    return site


def _parent_title(db: Session, site: Site, parent_id: int) -> str:
    """Родительская страница должна быть на сайте — её название менеджер видит
    в списке проектов и по нему сверяет, что не ошибся с id."""
    try:
        detail = open_site_client(db, site).get_page(parent_id)
    except SecretDecryptionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SiteAPIError as exc:
        if exc.status_code == 404:
            raise HTTPException(400, f"страница {parent_id} не найдена на сайте") from exc
        raise HTTPException(502, f"сайт не ответил: {exc}") from exc
    return (detail.get("title") or f"страница {parent_id}")[:300]


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    sites = db.scalars(select(Site).where(Site.projects_enabled.is_(True))
                       .order_by(Site.name)).all()
    return [_project_out(db, site) for site in sites]


@router.post("/projects", response_model=ProjectOut)
def create_project(payload: ProjectIn, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    site = db.get(Site, payload.site_id)
    if site is None:
        raise HTTPException(404, "сайт не найден")
    if site.projects_enabled:
        raise HTTPException(400, "проект для этого сайта уже добавлен")
    site.projects_parent_title = _parent_title(db, site, payload.parent_id)
    site.projects_parent_id = payload.parent_id
    site.projects_enabled = True
    db.commit()
    return _project_out(db, site)


@router.put("/projects/{site_id}", response_model=ProjectOut)
def update_project(site_id: int, payload: ProjectIn, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    """Смена родительской страницы. Страницы прежнего родителя следующая
    синхронизация пометит «удалена на сайте»."""
    site = _project_or_404(db, site_id)
    if is_busy(db, site.id):
        raise HTTPException(409, BUSY_TEXT)
    site.projects_parent_title = _parent_title(db, site, payload.parent_id)
    site.projects_parent_id = payload.parent_id
    db.commit()
    return _project_out(db, site)


@router.delete("/projects/{site_id}")
def delete_project(site_id: int, db: Session = Depends(get_db),
                   _user: User = Depends(get_current_user)):
    """Убирает сайт из раздела; страницы и даты рерайта в панели остаются."""
    site = _project_or_404(db, site_id)
    if is_busy(db, site.id):
        raise HTTPException(409, BUSY_TEXT)
    site.projects_enabled = False
    db.commit()
    return {"ok": True}


@router.post("/projects/{site_id}/run", response_model=ProjectOut)
def run_project(site_id: int, payload: RunIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    site = _project_or_404(db, site_id)
    if is_busy(db, site.id):
        raise HTTPException(409, "рерайт уже идёт")
    current = active_run(db, site.id)
    if current is not None and is_stale(db, current):
        abort_run(db, current, "прерван: цепочка задач оборвалась, рерайт запущен заново")
    run = ProjectRewriteRun(site_id=site.id, mode=payload.mode, created_by_id=user.id,
                            started_at=utcnow())
    db.add(run)
    db.commit()
    enqueue_rewrite_run(run.id)
    return _project_out(db, site)


@router.get("/projects/{site_id}/pages", response_model=list[PageOut])
def list_pages(site_id: int, db: Session = Depends(get_db),
               _user: User = Depends(get_current_user)):
    site = _project_or_404(db, site_id)
    rows = db.scalars(select(HouseProjectPage).where(HouseProjectPage.site_id == site.id)
                      .order_by(HouseProjectPage.title, HouseProjectPage.id)).all()
    return [_page_out(site, row) for row in rows]


def _page_or_404(db: Session, page_id: int) -> tuple[HouseProjectPage, Site]:
    row = db.get(HouseProjectPage, page_id)
    if row is None:
        raise HTTPException(404, "страница не найдена")
    return row, _project_or_404(db, row.site_id)


@router.get("/pages/{page_id}", response_model=PageDetailOut)
def get_page(page_id: int, db: Session = Depends(get_db),
             _user: User = Depends(get_current_user)):
    row, site = _page_or_404(db, page_id)
    return PageDetailOut(**_page_out(site, row), original_html=row.original_html,
                         rewritten_html=row.rewritten_html)


@router.post("/pages/{page_id}/rewrite", response_model=PageOut)
def rewrite_one(page_id: int, db: Session = Depends(get_db),
                _user: User = Depends(get_current_user)):
    row, site = _page_or_404(db, page_id)
    if row.status == "skipped":
        raise HTTPException(400, f"страница пропущена: {row.skip_reason}")
    run = active_run(db, site.id)
    if run is not None and not is_stale(db, run):
        raise HTTPException(409, BUSY_TEXT)
    if row.status in ("queued", "in_work") and not is_stuck(row) and not is_lost(row):
        raise HTTPException(409, "страница уже в работе")
    row.status, row.error_text, row.started_at = "queued", "", None
    row.updated_at = utcnow()
    db.commit()
    enqueue_rewrite_page(row.id, continue_run=False)
    return _page_out(site, row)
````

`execution/backend/app/main.py` — правка (дифф относительно main):

````diff
--- a/execution/backend/app/main.py
+++ b/execution/backend/app/main.py
@@ -28,6 +28,7 @@ from app.api import (
     category_meta,
     company_batches,
     company_imports,
+    house_projects,
     jobs,
     sites,
     tasks_status,
@@ -38,7 +39,7 @@ app = FastAPI(title="k1 content service")
 
 for module in (auth, sites, admin_sites, admin_settings, admin_prompts,
                admin_users, article_batches, company_imports, company_batches,
-               jobs, tasks_status, category_meta):
+               jobs, tasks_status, category_meta, house_projects):
     app.include_router(module.router)
 
 
````

- [ ] **Step 4: Запустить — проходит, затем полный регресс**

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_api_house_projects.py`
Expected: `15 passed`

Run: `docker compose run --rm --no-deps backend pytest -q`
Expected: `964 passed`

- [ ] **Commit**

```bash
git add execution/backend/app/api/house_projects.py execution/backend/app/main.py execution/backend/tests/test_api_house_projects.py
git commit -m "feat: проекты домов — API раздела

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 11: Фронтенд — API, статусы, меню, подписи

**Files:**
- Modify: `execution/frontend/src/api.ts`, `execution/frontend/src/statuses.ts`, `execution/frontend/src/App.tsx`, `execution/frontend/src/pages/JobsPage.tsx`, `execution/frontend/src/pages/AdminPromptsPage.tsx`

Тестов фронта в проекте нет — проверка сборкой (`tsc` + vite) в Task 13 и на стенде в Task 14.

- [ ] **Step 1: Правки**

`execution/frontend/src/api.ts` — правка (дифф относительно main):

````diff
--- a/execution/frontend/src/api.ts
+++ b/execution/frontend/src/api.ts
@@ -290,4 +290,43 @@ export const getMetaCategories = (siteId: number) =>
 export const regenerateMetaCategory = (id: number) =>
   api.post<CategoryMetaRow>(`/category-meta/categories/${id}/regenerate`).then(r => r.data)
 
+// --- проекты домов ---
+export type RewriteMode = 'new' | 'all'
+export interface RewriteRunState {
+  id: number; mode: RewriteMode; total: number; done: number; failed: number
+  started_at: string; stale: boolean
+}
+export interface HouseProject {
+  site_id: number; name: string; domain: string; parent_id: number; parent_title: string
+  pages: number; rewritten: number; new_count: number; error_count: number
+  last_rewritten_at: string | null; last_error: string; busy: boolean
+  run: RewriteRunState | null
+}
+export interface HouseProjectIn { site_id: number; parent_id: number }
+export interface HouseProjectPageRow {
+  id: number; remote_id: number; title: string; url: string; page_url: string
+  status: string; skip_reason: string; error_text: string; stuck: boolean
+  rewritten_at: string | null; rewrite_count: number
+  original_chars: number; rewritten_chars: number; updated_at: string
+}
+export interface HouseProjectPageDetail extends HouseProjectPageRow {
+  original_html: string; rewritten_html: string
+}
+export const getHouseProjects = () =>
+  api.get<HouseProject[]>('/house-projects/projects').then(r => r.data)
+export const createHouseProject = (d: HouseProjectIn) =>
+  api.post<HouseProject>('/house-projects/projects', d).then(r => r.data)
+export const updateHouseProject = (siteId: number, d: HouseProjectIn) =>
+  api.put<HouseProject>(`/house-projects/projects/${siteId}`, d).then(r => r.data)
+export const deleteHouseProject = (siteId: number) =>
+  api.delete(`/house-projects/projects/${siteId}`)
+export const runHouseProject = (siteId: number, mode: RewriteMode) =>
+  api.post<HouseProject>(`/house-projects/projects/${siteId}/run`, { mode }).then(r => r.data)
+export const getHouseProjectPages = (siteId: number) =>
+  api.get<HouseProjectPageRow[]>(`/house-projects/projects/${siteId}/pages`).then(r => r.data)
+export const getHouseProjectPage = (id: number) =>
+  api.get<HouseProjectPageDetail>(`/house-projects/pages/${id}`).then(r => r.data)
+export const rewriteHouseProjectPage = (id: number) =>
+  api.post<HouseProjectPageRow>(`/house-projects/pages/${id}/rewrite`).then(r => r.data)
+
 export default api
````

`execution/frontend/src/statuses.ts` — правка (дифф относительно main):

````diff
--- a/execution/frontend/src/statuses.ts
+++ b/execution/frontend/src/statuses.ts
@@ -36,3 +36,13 @@ export const CATEGORY_META_STATUS: Record<string, { color: string; label: string
   failed: { color: 'error', label: 'Ошибка' },
   skipped: { color: 'default', label: 'Пропущена' },
 }
+
+/** Статусы страницы в разделе «Проекты домов». */
+export const HOUSE_PROJECT_PAGE_STATUS: Record<string, { color: string; label: string }> = {
+  new: { color: 'default', label: 'Ещё не переписана' },
+  queued: { color: 'default', label: 'В очереди' },
+  in_work: { color: 'processing', label: 'В работе' },
+  done: { color: 'success', label: 'Переписана' },
+  failed: { color: 'error', label: 'Ошибка' },
+  skipped: { color: 'default', label: 'Пропущена' },
+}
````

`execution/frontend/src/App.tsx` — правка (дифф относительно main):

````diff
--- a/execution/frontend/src/App.tsx
+++ b/execution/frontend/src/App.tsx
@@ -4,7 +4,7 @@ import { ConfigProvider, Layout, Menu, Drawer, Button, Dropdown } from 'antd'
 import {
   FileTextOutlined, HomeOutlined, GlobalOutlined, BulbOutlined,
   SettingOutlined, TeamOutlined, HistoryOutlined, MenuOutlined,
-  CloseOutlined, UserOutlined, NotificationOutlined, TagsOutlined,
+  CloseOutlined, UserOutlined, NotificationOutlined, TagsOutlined, BuildOutlined,
 } from '@ant-design/icons'
 import { useAuth } from './auth'
 import { logout } from './api'
@@ -14,6 +14,7 @@ import BatchPage from './pages/BatchPage'
 import BuildersPage from './pages/BuildersPage'
 import BuilderBatchPage from './pages/BuilderBatchPage'
 import CategoryMetaPage from './pages/CategoryMetaPage'
+import HouseProjectsPage from './pages/HouseProjectsPage'
 import JobsPage from './pages/JobsPage'
 import AdminSitesPage from './pages/AdminSitesPage'
 import AdminPromptsPage from './pages/AdminPromptsPage'
@@ -27,6 +28,7 @@ const navItems = [
   { key: '/articles', label: 'Статьи', icon: <FileTextOutlined />, admin: false },
   { key: '/builders', label: 'Строители', icon: <HomeOutlined />, admin: false },
   { key: '/category-meta', label: 'Метатеги категорий', icon: <TagsOutlined />, admin: false },
+  { key: '/house-projects', label: 'Проекты домов', icon: <BuildOutlined />, admin: false },
   { key: '/jobs', label: 'Журнал', icon: <HistoryOutlined />, admin: false },
   // Сайты/Промпты доступны и менеджеру (require_role("admin", "manager")
   // на бэкенде, app/api/admin_sites.py и app/api/admin_prompts.py) — admin:
@@ -207,6 +209,7 @@ function Shell() {
             <Route path="/builders" element={<BuildersPage />} />
             <Route path="/builders/:id" element={<BuilderBatchPage />} />
             <Route path="/category-meta" element={<CategoryMetaPage />} />
+            <Route path="/house-projects" element={<HouseProjectsPage />} />
             <Route path="/jobs" element={<JobsPage />} />
             <Route path="/admin/sites" element={<AdminSitesPage />} />
             <Route path="/admin/prompts" element={<AdminPromptsPage />} />
````

`execution/frontend/src/pages/JobsPage.tsx` — правка (дифф относительно main):

````diff
--- a/execution/frontend/src/pages/JobsPage.tsx
+++ b/execution/frontend/src/pages/JobsPage.tsx
@@ -12,6 +12,8 @@ const KIND: Record<string, string> = {
   // оставлено для уже существующих в БД исторических job-записей.
   regenerate_article_images: 'Перегенерация картинок',
   regenerate_article: 'Перегенерация статьи',
+  project_rewrite_run: 'Рерайт проектов домов',
+  project_rewrite_page: 'Рерайт страницы проекта',
 }
 
 export default function JobsPage() {
````

`execution/frontend/src/pages/AdminPromptsPage.tsx` — правка (дифф относительно main):

````diff
--- a/execution/frontend/src/pages/AdminPromptsPage.tsx
+++ b/execution/frontend/src/pages/AdminPromptsPage.tsx
@@ -13,6 +13,7 @@ const KEYS = [
   { key: 'category_variants', label: 'Метатеги: проверка названий', vars: { site_name: 'Стройбаза', category_name: 'Пенопласт', category_path: 'Теплоизоляция / Пенопласт', variants: ['пенопласт — 29949: пенопласт, пенопласт купить, пенопласт цена', 'псб — 274344: псб, псб банк, псб онлайн'] } },
   { key: 'category_meta', label: 'Метатеги: теги', vars: { site_name: 'Стройбаза', site_description: 'Интернет-магазин стройматериалов в Москве, доставка по городу', category_name: 'Фанера', category_path: 'Листовые материалы / Фанера', form_nominative: 'фанера', form_buy: 'купить фанеру', form_price: 'цена фанеры', chosen_form: 'nominative', sell_word: 'купить', city_in: 'в Москве', brand: 'Стройбаза', total_count: 96275, phrases: ['фанера — 96275', 'фанера купить — 12238', 'фанера цена — 5300'], alternatives: ['фанерный лист — 198: фанерный лист'], violations: [] } },
   { key: 'category_seo_text', label: 'Метатеги: SEO-текст', vars: { site_name: 'Стройбаза', site_description: 'Интернет-магазин стройматериалов в Москве, доставка по городу', category_name: 'Фанера', category_path: 'Листовые материалы / Фанера', form_nominative: 'фанера', city_in: 'в Москве', h1: 'Фанера в Москве', keywords: ['фанера купить', 'фанера цена', 'фанера влагостойкая'], phrases: ['фанера — 96275', 'фанера купить — 12238', 'ламинированная фанера — 8428'], products: ['Фанера ФК 1525х1525х10 мм сорт 2/4', 'Фанера ФСФ 2440х1220х18 мм'], violations: [] } },
+  { key: 'project_rewrite', label: 'Проекты домов: рерайт', vars: { site_name: 'Стройбаза', tone_of_voice: '', title: 'Проект дома из кирпича от 80 м²', description_html: '<p>Представляем проект уютного дома площадью 82.64 м² с террасой 9.06 м².</p><h3>Ключевые преимущества:</h3><ul><li>Монолитная железобетонная плита.</li></ul>', char_count: 120, violations: [] } },
 ]
 
 export default function AdminPromptsPage() {
````

`App.tsx` импортирует `./pages/HouseProjectsPage`, которого до Task 12 нет: между задачами 11 и 12 фронт не собирается — это ожидаемо, сборка — в Task 13.

- [ ] **Commit**

```bash
git add execution/frontend/src/api.ts execution/frontend/src/statuses.ts execution/frontend/src/App.tsx execution/frontend/src/pages/JobsPage.tsx execution/frontend/src/pages/AdminPromptsPage.tsx
git commit -m "feat: проекты домов — вызовы API, статусы страниц, пункт меню

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 12: Фронтенд — экраны раздела

**Files:**
- Create: `execution/frontend/src/pages/HouseProjectsPage.tsx`
- Create: `execution/frontend/src/pages/HouseProjectPages.tsx`

Карточка страницы показывает HTML описаний текстом по блокам (`p`/`h3`/`li`), без `dangerouslySetInnerHTML` — как SEO-текст в «Метатегах».

- [ ] **Step 1: Список проектов**

`execution/frontend/src/pages/HouseProjectsPage.tsx`:

````tsx
import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import {
  Button, Card, Dropdown, Form, InputNumber, Modal, Popconfirm, Select, Space, Table, Tag,
  Typography, message,
} from 'antd'
import { DeleteOutlined, DownOutlined, EditOutlined, PlusOutlined, SyncOutlined } from '@ant-design/icons'
import {
  HouseProject, HouseProjectIn, RewriteMode, SiteBrief, createHouseProject, deleteHouseProject,
  getHouseProjects, getSites, runHouseProject, updateHouseProject,
} from '../api'
import HelpButton from '../help'
import HouseProjectPages from './HouseProjectPages'

// Оборвавшийся запуск проект не держит — «Запустить рерайт» снова доступна.
const isBusy = (p: HouseProject) => p.busy

function ProjectState({ project }: { project: HouseProject }) {
  const { run } = project
  if (run && run.stale) {
    return <Tag color="error">Рерайт оборвался — запустите заново</Tag>
  }
  if (run) {
    return (
      <Tag color="processing">
        {run.total ? `В работе: ${run.done + run.failed} из ${run.total}` : 'Читаем страницы с сайта'}
      </Tag>
    )
  }
  if (project.busy) return <Tag color="processing">Переписывается страница</Tag>
  return (
    <Space direction="vertical" size={2}>
      <Typography.Text>
        {project.pages ? `Переписано ${project.rewritten} из ${project.pages}` : 'Страницы ещё не читались'}
      </Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {project.last_rewritten_at
          ? `Последний рерайт ${dayjs(project.last_rewritten_at).format('DD.MM.YYYY HH:mm')}`
          : 'Рерайта ещё не было'}
      </Typography.Text>
      {project.last_error && (
        <Typography.Text type="danger" style={{ fontSize: 12 }}>
          Последний запуск: {project.last_error}
        </Typography.Text>
      )}
    </Space>
  )
}

function ProjectModal({ project, sites, usedSiteIds, onClose, onSaved }: {
  project: HouseProject | null
  sites: SiteBrief[]
  usedSiteIds: number[]
  onClose: () => void
  onSaved: () => void
}) {
  const [form] = Form.useForm<HouseProjectIn>()
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (project) form.setFieldsValue({ site_id: project.site_id, parent_id: project.parent_id })
  }, [project])

  const submit = async (values: HouseProjectIn) => {
    setSaving(true)
    try {
      const saved = project
        ? await updateHouseProject(project.site_id, values)
        : await createHouseProject(values)
      message.success(`Проект сохранён: «${saved.parent_title}»`)
      onSaved()
    } catch { /* сообщение уже показал интерцептор */ }
    finally { setSaving(false) }
  }

  const siteOptions = sites
    .filter(s => project ? s.id === project.site_id : !usedSiteIds.includes(s.id))
    .map(s => ({ value: s.id, label: `${s.name} (${s.domain})` }))

  return (
    <Modal open title={project ? `Проект — ${project.name}` : 'Новый проект'} okText="Сохранить"
           cancelText="Отмена" confirmLoading={saving} onCancel={onClose}
           onOk={() => form.submit()} destroyOnClose>
      <Form form={form} layout="vertical" onFinish={submit} requiredMark={false}>
        <Form.Item name="site_id" label="Сайт" rules={[{ required: true, message: 'Выберите сайт' }]}>
          <Select disabled={Boolean(project)} options={siteOptions} placeholder="Сайт из раздела «Сайты»" />
        </Form.Item>
        <Form.Item name="parent_id" label="ID родительской страницы"
                   rules={[{ required: true, message: 'Укажите ID страницы' }]}
                   extra="Страница раздела проектов в админке сайта (на stroybaza-samara.ru — 15). Переписываются её прямые подстраницы">
          <InputNumber min={1} style={{ width: '100%' }} placeholder="15" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function HouseProjectsPage() {
  const [projects, setProjects] = useState<HouseProject[]>([])
  const [sites, setSites] = useState<SiteBrief[]>([])
  const [editing, setEditing] = useState<HouseProject | 'new' | null>(null)
  const [busySite, setBusySite] = useState<number | null>(null)

  const load = () => getHouseProjects().then(setProjects)

  useEffect(() => { load(); getSites().then(setSites) }, [])

  useEffect(() => {
    if (!projects.some(isBusy)) return
    const timer = setInterval(load, 10000)
    return () => clearInterval(timer)
  }, [projects])

  const start = async (project: HouseProject, mode: RewriteMode) => {
    setBusySite(project.site_id)
    try {
      await runHouseProject(project.site_id, mode)
      message.success('Рерайт запущен — идёт в фоне')
      await load()
    } catch { /* сообщение уже показал интерцептор */ }
    finally { setBusySite(null) }
  }

  const confirmRun = (project: HouseProject, mode: RewriteMode) => {
    Modal.confirm({
      title: mode === 'new' ? 'Переписать новые страницы?' : 'Переписать все страницы заново?',
      content: mode === 'new'
        ? 'Возьмутся страницы, которые ещё не переписывались, и страницы с ошибкой. Новый текст сразу запишется на сайт.'
        : 'Все страницы раздела перепишутся заново от оригинала. Новый текст сразу запишется на сайт.',
      okText: 'Запустить', cancelText: 'Отмена',
      onOk: () => start(project, mode),
    })
  }

  const remove = async (siteId: number) => {
    try { await deleteHouseProject(siteId); await load() }
    catch { /* сообщение уже показал интерцептор */ }
  }

  // Пока страницы не читались, числа неизвестны: список прочитается при запуске.
  const modeLabel = (p: HouseProject, mode: RewriteMode) => {
    const label = mode === 'new' ? 'Только новые' : 'Все заново'
    if (!p.pages) return label
    return `${label} (${mode === 'new' ? p.new_count : p.pages})`
  }

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} wrap>
        <Typography.Title level={4} style={{ margin: 0 }}>Проекты домов</Typography.Title>
        <Space>
          <HelpButton section="houseProjects" />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing('new')}>
            Добавить проект
          </Button>
        </Space>
      </Space>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="site_id"
          dataSource={projects}
          pagination={false}
          scroll={{ x: 720 }}
          locale={{ emptyText: 'Проектов пока нет — добавьте сайт' }}
          columns={[
            {
              title: 'Сайт',
              render: (_, p: HouseProject) => (
                <Space direction="vertical" size={0}>
                  <Typography.Text strong>{p.name}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{p.domain}</Typography.Text>
                  {p.error_count > 0 && (
                    <Tag color="error" style={{ marginTop: 4 }}>Страниц с ошибкой: {p.error_count}</Tag>
                  )}
                </Space>
              ),
            },
            {
              title: 'Раздел', width: 200,
              render: (_, p: HouseProject) => `${p.parent_title} (id ${p.parent_id})`,
            },
            { title: 'Состояние', width: 260, render: (_, p: HouseProject) => <ProjectState project={p} /> },
            {
              title: '', width: 260,
              render: (_, p: HouseProject) => (
                <Space>
                  <Dropdown disabled={isBusy(p)} trigger={['click']} menu={{
                    items: [
                      { key: 'new', label: modeLabel(p, 'new') },
                      { key: 'all', label: modeLabel(p, 'all') },
                    ],
                    onClick: ({ key }) => confirmRun(p, key as RewriteMode),
                  }}>
                    <Button icon={<SyncOutlined />} loading={busySite === p.site_id} disabled={isBusy(p)}>
                      Запустить рерайт <DownOutlined />
                    </Button>
                  </Dropdown>
                  <Button type="text" icon={<EditOutlined />} disabled={isBusy(p)}
                          onClick={() => setEditing(p)} />
                  <Popconfirm title="Убрать проект из раздела? Тексты на сайте останутся."
                              onConfirm={() => remove(p.site_id)}>
                    <Button type="text" icon={<DeleteOutlined />} disabled={isBusy(p)} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
          expandable={{
            expandedRowRender: (p: HouseProject) => (
              <HouseProjectPages siteId={p.site_id} busy={isBusy(p)} onChanged={load} />
            ),
          }}
        />
      </Card>

      {editing && (
        <ProjectModal
          project={editing === 'new' ? null : editing}
          sites={sites}
          usedSiteIds={projects.map(p => p.site_id)}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load() }}
        />
      )}
    </>
  )
}
````

- [ ] **Step 2: Страницы проекта и карточка**

`execution/frontend/src/pages/HouseProjectPages.tsx`:

````tsx
import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import { Alert, Button, Col, Drawer, Popconfirm, Row, Space, Table, Tag, Typography, message } from 'antd'
import { LinkOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  HouseProjectPageDetail, HouseProjectPageRow, getHouseProjectPage, getHouseProjectPages,
  rewriteHouseProjectPage,
} from '../api'
import { HOUSE_PROJECT_PAGE_STATUS } from '../statuses'

function StatusTag({ row }: { row: HouseProjectPageRow }) {
  if (row.stuck) return <Tag color="error">Похоже, зависла</Tag>
  const status = HOUSE_PROJECT_PAGE_STATUS[row.status]
  return <Tag color={status?.color}>{status?.label ?? row.status}</Tag>
}

// Описание — HTML с p/h3/ul; в карточке показываем текстом, без innerHTML.
function blocks(html: string): { heading: boolean; item: boolean; text: string }[] {
  return html.split(/(?=<(?:p|h3|li)\b)/i)
    .map(part => ({
      heading: /^<h3\b/i.test(part),
      item: /^<li\b/i.test(part),
      text: part.replace(/<br\s*\/?>/gi, ' ').replace(/<[^>]+>/g, ' ')
        .replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim(),
    }))
    .filter(block => block.text)
}

function Description({ title, html, chars }: { title: string; html: string; chars: number }) {
  return (
    <>
      <Typography.Title level={5}>{title} · {chars} символов</Typography.Title>
      {html
        ? blocks(html).map((block, i) => (
          <Typography.Paragraph key={i} strong={block.heading} style={{ marginBottom: 8 }}>
            {block.item ? `• ${block.text}` : block.text}
          </Typography.Paragraph>
        ))
        : <Typography.Text type="secondary">—</Typography.Text>}
    </>
  )
}

function PageDrawer({ pageId, busy, onClose, onChanged }: {
  pageId: number | null
  busy: boolean
  onClose: () => void
  onChanged: () => void
}) {
  const [page, setPage] = useState<HouseProjectPageDetail | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setPage(null)
    if (pageId) getHouseProjectPage(pageId).then(setPage)
  }, [pageId])

  if (!pageId) return null

  const canRewrite = page !== null && !busy
    && (['new', 'done', 'failed'].includes(page.status) || page.stuck)

  const rewrite = async () => {
    if (!page) return
    setSaving(true)
    try {
      await rewriteHouseProjectPage(page.id)
      message.success('Рерайт страницы поставлен в очередь')
      onChanged()
      onClose()
    } catch { /* сообщение уже показал интерцептор */ }
    finally { setSaving(false) }
  }

  return (
    <Drawer open width={900} title={page?.title ?? 'Загрузка…'} onClose={onClose}
            extra={canRewrite && (
              <Popconfirm title="Переписать описание заново?"
                          description="Рерайт делается от оригинала, новый текст сразу запишется на сайт."
                          onConfirm={rewrite}>
                <Button icon={<ReloadOutlined />} loading={saving}>Переписать</Button>
              </Popconfirm>
            )}>
      {page && (
        <>
          <Typography.Paragraph type="secondary">
            <a href={page.page_url} target="_blank" rel="noreferrer">{page.url}</a>
            {page.rewritten_at && <> · переписана {dayjs(page.rewritten_at).format('DD.MM.YYYY HH:mm')}
              {page.rewrite_count > 1 && <>, всего раз: {page.rewrite_count}</>}</>}
          </Typography.Paragraph>
          {page.skip_reason && (
            <Alert type="info" showIcon style={{ marginBottom: 16 }} message={`Пропущена: ${page.skip_reason}`} />
          )}
          {page.error_text && (
            <Alert type="error" showIcon style={{ marginBottom: 16 }} message="Ошибка" description={page.error_text} />
          )}
          <Row gutter={24}>
            <Col xs={24} md={12}>
              <Description title="Оригинал" html={page.original_html} chars={page.original_chars} />
            </Col>
            <Col xs={24} md={12}>
              <Description title="Сейчас на сайте" html={page.rewritten_html} chars={page.rewritten_chars} />
            </Col>
          </Row>
        </>
      )}
    </Drawer>
  )
}

export default function HouseProjectPages({ siteId, busy, onChanged }: {
  siteId: number
  busy: boolean
  onChanged: () => void
}) {
  const [rows, setRows] = useState<HouseProjectPageRow[] | null>(null)
  const [openId, setOpenId] = useState<number | null>(null)

  const load = () => getHouseProjectPages(siteId).then(setRows)

  useEffect(() => { load() }, [siteId])

  useEffect(() => {
    const working = busy || (rows ?? []).some(r => r.status === 'queued' || r.status === 'in_work')
    if (!working) return
    const timer = setInterval(load, 10000)
    return () => clearInterval(timer)
  }, [rows, busy])

  if (rows === null) return <Typography.Text type="secondary">Загрузка…</Typography.Text>
  if (!rows.length) {
    return <Typography.Text type="secondary">Страниц пока нет — нажмите «Запустить рерайт»</Typography.Text>
  }

  return (
    <>
      <Table<HouseProjectPageRow>
        rowKey="id"
        size="small"
        dataSource={rows}
        pagination={false}
        columns={[
          {
            title: 'Страница', dataIndex: 'title',
            render: (title: string, r) => (
              <Space size={4}>
                <Button type="link" style={{ padding: 0, height: 'auto', whiteSpace: 'normal', textAlign: 'left' }}
                        onClick={() => setOpenId(r.id)}>
                  {title}
                </Button>
                {r.page_url && (
                  <a href={r.page_url} target="_blank" rel="noreferrer"><LinkOutlined /></a>
                )}
              </Space>
            ),
          },
          { title: 'Статус', width: 170, render: (_, r) => <StatusTag row={r} /> },
          {
            title: 'Рерайт', width: 150,
            render: (_, r) => r.rewritten_at ? dayjs(r.rewritten_at).format('DD.MM.YYYY HH:mm') : '—',
          },
          { title: 'Раз', width: 60, dataIndex: 'rewrite_count' },
        ]}
      />
      <PageDrawer pageId={openId} busy={busy} onClose={() => setOpenId(null)}
                  onChanged={() => { load(); onChanged() }} />
    </>
  )
}
````

- [ ] **Commit**

```bash
git add execution/frontend/src/pages/HouseProjectsPage.tsx execution/frontend/src/pages/HouseProjectPages.tsx
git commit -m "feat: проекты домов — экран проектов и страниц

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 13: Справка и «Доработки», сборка

Правило проекта: справка (`help.tsx`) и запись в `changelog.ts` — в том же деплое, что и раздел. **Дата записи — день выкатки на прод**; если выкатка не 2026-10-18, поправьте `date`.

**Files:**
- Modify: `execution/frontend/src/help.tsx`, `execution/frontend/src/changelog.ts`

- [ ] **Step 1: Правки**

`execution/frontend/src/help.tsx` — правка (дифф относительно main):

````diff
--- a/execution/frontend/src/help.tsx
+++ b/execution/frontend/src/help.tsx
@@ -11,7 +11,7 @@ import { ReactNode, useState } from 'react'
 import { Button, Modal, Typography } from 'antd'
 import { QuestionCircleOutlined } from '@ant-design/icons'
 
-export type HelpSection = 'articles' | 'builders' | 'categoryMeta'
+export type HelpSection = 'articles' | 'builders' | 'categoryMeta' | 'houseProjects'
 
 interface HelpContent {
   title: string
@@ -113,6 +113,36 @@ const HELP: Record<HelpSection, HelpContent> = {
         «Обновить метатеги».</>,
     ],
   },
+  houseProjects: {
+    title: 'Как работают «Проекты домов»',
+    steps: [
+      <>«Добавить проект» — сайт и ID родительской страницы раздела проектов в
+        админке сайта (на stroybaza-samara.ru — 15). Панель проверяет, что такая
+        страница есть, и показывает её название.</>,
+      <>«Запустить рерайт» — «Только новые» (ещё не переписанные и с ошибкой)
+        или «Все заново». Панель читает с сайта все подстраницы раздела: новые,
+        скопированные с другого сайта, добавляются сами, пропавшие помечаются
+        «удалена на сайте».</>,
+      <>На каждой странице ИИ переписывает только блок «Описание проекта».
+        Характеристики, фото, планировки и форма расчёта не трогаются. Числа,
+        размеры, материалы и конструкция остаются как были, объём — примерно
+        тот же (±10 %).</>,
+      <>Панель проверяет текст: объём, все числа из оригинала на месте, те же
+        подзаголовки и пункты списков, текст действительно переписан. При
+        нарушении — одна повторная попытка, потом ошибка; на сайт уходит только
+        проверенный текст.</>,
+      <>Новое описание сразу записывается на страницу. Дата рерайта видна в
+        списке страниц.</>,
+    ],
+    notes: [
+      <>Повторный рерайт всегда делается от оригинала — того описания, что было
+        на странице до первого рерайта. Если описание поправили в админке сайта
+        после рерайта, оригиналом становится поправленный текст.</>,
+      <>Любую страницу можно переписать заново из её карточки — там же видны
+        оригинал и текущий текст.</>,
+      <>Страница без блока «Описание проекта» пропускается.</>,
+    ],
+  },
 }
 
 export default function HelpButton({ section }: { section: HelpSection }) {
````

`execution/frontend/src/changelog.ts` — правка (дифф относительно main):

````diff
--- a/execution/frontend/src/changelog.ts
+++ b/execution/frontend/src/changelog.ts
@@ -36,6 +36,17 @@ export const KIND_COLOR: Record<ChangeKind, string> = {
 }
 
 export const CHANGELOG: ChangeEntry[] = [
+  {
+    date: '2026-10-18',
+    kind: 'feature',
+    title: 'Проекты домов: рерайт описаний',
+    text: 'Новый раздел «Проекты домов» переписывает описания страниц раздела проектов, '
+      + 'который скопирован с сайта на сайт. В проекте указывается сайт и ID родительской '
+      + 'страницы; «Запустить рерайт» переписывает только новые страницы или все заново. '
+      + 'Меняется только блок «Описание проекта»: характеристики, фото и планировки не '
+      + 'трогаются, числа и материалы остаются как были, объём — примерно тот же. Дата '
+      + 'рерайта видна у каждой страницы, повторный рерайт делается от оригинала.',
+  },
   {
     date: '2026-10-03',
     kind: 'feature',
````

- [ ] **Step 2: Сборка**

Run: `docker compose run --rm frontend sh -c "npm install && npm run build"`
Expected: `✓ built`, без ошибок `tsc` (предупреждение о размере чанка > 500 kB было и до раздела)

- [ ] **Commit**

```bash
git add execution/frontend/src/help.tsx execution/frontend/src/changelog.ts
git commit -m "docs: проекты домов — справка раздела и запись в «Доработки»

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 14: Живая проверка на стенде

Скилл `running-local-panel`. **Стенд пишет в настоящие сайты** — любая запись на stroybaza-samara.ru только после явного «да» владельца, по одной странице.

- [ ] **Step 1: Стенд и проект (только чтение сайта)**

Поднять стенд, в «Проекты домов» → «Добавить проект»: stroybaza-samara.ru, ID 15. Ожидается тост «Проект сохранён: «Проекты домов»», в строке — «Проекты домов (id 15)», «Страницы ещё не читались». ID 999 — ошибка «страница 999 не найдена на сайте».

- [ ] **Step 2: Спросить владельца**

Показать: какую страницу перепишем (предложить id 16 «Проект дома из кирпича от 80 м²»), что текст сразу запишется на сайт, что откат — повторный рерайт или правка в админке. Без «да» — дальше не идти.

- [ ] **Step 3: Одна страница**

Нельзя переписать страницу, пока панель её не знает, а синхронизация идёт только в запуске. Поэтому на стенде, без постановки в очередь, — синхронизация вручную:

```bash
docker compose run --rm -T backend python <<'PY'
from app.db import SessionLocal
from app.models.site import Site
from app.api.admin_sites import open_client
from app.house_projects.sync import sync_pages

db = SessionLocal()
site = db.query(Site).filter_by(domain="stroybaza-samara.ru").one()
print(len(sync_pages(db, site, open_client(db, site))))
db.commit()
PY
```

Expected: `56`. Раскрыть проект — 56 страниц «Ещё не переписана». Открыть карточку выбранной страницы → «Переписать». Через минуту: статус «Переписана», дата, в карточке оригинал и новый текст, объём в пределах ±10 %.

- [ ] **Step 4: Проверить страницу на сайте**

Открыть страницу на сайте: описание новое; характеристики, фото, планировки, кнопки и форма «Рассчитаем стоимость» на месте и работают. Показать владельцу оригинал и новый текст из карточки.

- [ ] **Step 5: Повтор от оригинала**

«Переписать» ещё раз: `rewrite_count` = 2, в карточке «Оригинал» прежний, текст другой.

- [ ] **Step 6: Итог**

Полный запуск по разделу (56 страниц, ≈ 56 × 0,24 ₽, около часа) — решение владельца; из этой задачи не запускать. Если по пробе нужен другой порог отличия или объёма — правка в `app/house_projects/validate.py`, тестах и тексте этого плана.
