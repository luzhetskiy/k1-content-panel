# Перегенерация текста/картинок/обложки статьи — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Расширить существующую кнопку «Перегенерировать картинки» до трёх независимых, компонуемых частей — текст, картинки внутри статьи, обложка — и дать два интерфейса запуска: компактные кнопки на странице партии и инструмент «по ID статьи» на странице «Статьи».

**Architecture:** `ArticleBuilder` получает два новых метода (`regenerate_text`, `regenerate_cover`) и оркестратор `regenerate(text, images, cover)`, который вызывает нужные части в фиксированном порядке (текст всегда первым — иначе замена путей картинок в `body_html` не найдёт, что менять) и агрегирует ошибки. Всё выполняется одной Celery-задачей — это же и есть защита от гонки текста/картинок за `body_html`. Флаг `images_regenerating` переименовывается в `regenerating` (общий для всех трёх частей).

**Tech Stack:** те же, что и во всём бэкенде/фронтенде проекта (FastAPI, SQLAlchemy, Alembic, Celery, pytest; React + antd).

**Спека:** `directions/2026-09-09-article-full-regeneration-design.md`

---

## Структура файлов

```
execution/backend/
  alembic/versions/<new>_rename_images_regenerating.py   Create: rename articles.images_regenerating -> regenerating
  app/models/article.py                                  Modify: Article.images_regenerating -> regenerating
  app/sites/client.py                                     Modify: update_page_text(title=, meta_description=, meta_keywords=)
  app/articles/builder.py                                 Modify: _generate_body(image_paths=), _current_content_image_paths(), regenerate_text(), regenerate_cover(), regenerate(), regenerate_article_for()
  app/tasks.py                                             Modify: regenerate_article_sync/regenerate_article (rename + text/images/cover)
  app/api/article_batches.py                              Modify: POST /articles/{id}/regenerate, RegenerateIn, _regen_time_limits(text, image_count, cover)
  tests/test_sites_client.py                               Modify: update_page_text title/meta tests
  tests/test_articles_builder.py                           Modify: rename images_regenerating; +regenerate_text/regenerate_cover/regenerate тесты
  tests/test_tasks.py                                       Modify: rename regenerate_article_images_sync -> regenerate_article_sync
  tests/test_api_batches.py                                 Modify: /regenerate-images -> /regenerate, no_celery fixture

execution/frontend/
  src/api.ts                    Modify: ArticleRow.regenerating, regenerateArticle(id, parts)
  src/pages/BatchPage.tsx        Modify: 3 компактные кнопки вместо одной
  src/pages/ArticlesPage.tsx     Modify: модалка «Перегенерировать по ID»
```

## Как запускать

```bash
cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/execution
docker compose run --rm --no-deps backend pytest -q      # тесты без БД (SQLite in-memory)
docker compose up -d postgres redis                        # для проверки миграции на реальном Postgres
docker compose run --rm backend alembic upgrade head
docker compose run --rm frontend sh -c "npm install && npm run build"   # tsc + vite build
```

---

### Task 1: Переименование `images_regenerating` → `regenerating`

**Files:**
- Create: `execution/backend/alembic/versions/<generated>_rename_images_regenerating.py`
- Modify: `execution/backend/app/models/article.py:119`
- Modify: `execution/backend/app/articles/builder.py` (4 occurrences — 3 в коде `regenerate_content_images`, 1 в комментарии)
- Modify: `execution/backend/app/tasks.py` (в `regenerate_article_images_sync`, будет переименована в Task 6 — здесь только меняем поле)
- Modify: `execution/backend/app/api/article_batches.py` (`ArticleOut.images_regenerating`, `_to_out`, эндпоинт)
- Modify: `execution/backend/tests/test_models_article.py:149`
- Modify: `execution/backend/tests/test_articles_builder.py` (9 occurrences)
- Modify: `execution/backend/tests/test_tasks.py` (8 occurrences)
- Modify: `execution/backend/tests/test_api_batches.py` (4 occurrences)
- Modify: `execution/frontend/src/api.ts:93`
- Modify: `execution/frontend/src/pages/BatchPage.tsx` (3 occurrences)

**Не трогать:** `execution/backend/alembic/versions/bcba3fe8e22e_article_image_regeneration.py` — это исторический файл, зафиксировавший, что колонка была ДОБАВЛЕНА под именем `images_regenerating`; переименование делает НОВАЯ миграция через `alter_column`, а не правка старой.

- [ ] **Step 1: Проверить, что переименование безопасно как простая замена строки**

Run: `grep -rn "images_regenerating[a-zA-Z_]" execution/backend execution/frontend --include="*.py" --include="*.ts" --include="*.tsx"`
Expected: пусто (нет более длинных идентификаторов вроде `images_regenerating_count`, которые сломались бы от наивной замены подстроки).

- [ ] **Step 2: Сгенерировать пустую ревизию**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/execution && docker compose run --rm backend alembic revision -m "rename images_regenerating to regenerating"`

Expected: создан файл `execution/backend/alembic/versions/<hash>_rename_images_regenerating_to_regenerating.py` с `down_revision = 'a2daefb8e7f3'` (текущая голова, см. `a2daefb8e7f3_site_reference_image_ratios.py`).

- [ ] **Step 3: Заполнить upgrade/downgrade**

```python
"""rename images_regenerating to regenerating

Флаг раньше означал ровно одно: «идёт перегенерация картинок». Теперь под
ним могут идти три разных вида работы (текст/картинки/обложка,
directions/2026-09-09-article-full-regeneration-design.md) — оставлять имя
images_regenerating было бы прямой ложью для двух из трёх кнопок.

Revision ID: <как сгенерировал alembic>
Revises: a2daefb8e7f3
Create Date: <как сгенерировал alembic>

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '<как сгенерировал alembic>'
down_revision: Union[str, None] = 'a2daefb8e7f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('articles', 'images_regenerating', new_column_name='regenerating',
                    existing_type=sa.Boolean(), existing_nullable=False,
                    existing_server_default=sa.false())


def downgrade() -> None:
    op.alter_column('articles', 'regenerating', new_column_name='images_regenerating',
                    existing_type=sa.Boolean(), existing_nullable=False,
                    existing_server_default=sa.false())
```

- [ ] **Step 4: Применить миграцию на реальном Postgres и проверить откат**

```bash
docker compose up -d postgres
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend alembic downgrade -1
docker compose run --rm backend alembic upgrade head
```
Expected: все три команды без ошибок.

- [ ] **Step 5: Переименовать поле во всех местах**

Run (из `execution/`, mac/BSD sed):
```bash
for f in backend/app/models/article.py \
        backend/app/articles/builder.py \
        backend/app/tasks.py \
        backend/app/api/article_batches.py \
        backend/tests/test_models_article.py \
        backend/tests/test_articles_builder.py \
        backend/tests/test_tasks.py \
        backend/tests/test_api_batches.py \
        frontend/src/api.ts \
        frontend/src/pages/BatchPage.tsx; do
  sed -i '' 's/images_regenerating/regenerating/g' "$f"
done
```

- [ ] **Step 6: Проверить, что замена не задела исторический файл миграции**

Run: `grep -c images_regenerating execution/backend/alembic/versions/bcba3fe8e22e_article_image_regeneration.py`
Expected: `3` (докстринг ревизии + `op.add_column`/`op.drop_column` внутри исторической миграции — она не входила в список файлов Step 5).

- [ ] **Step 7: Запустить тесты**

Run: `docker compose run --rm --no-deps backend pytest -q`
Expected: все тесты проходят (SQLite-тесты берут схему из модели, не из миграции — переименование в модели достаточно).

- [ ] **Step 8: Проверить фронтенд**

Run: `docker compose run --rm --no-deps frontend sh -c "npm install && npx tsc --noEmit"`
Expected: без ошибок.

- [ ] **Step 9: Commit**

```bash
git add execution/backend/alembic/versions/*_rename_images_regenerating*.py \
       execution/backend/app/models/article.py execution/backend/app/articles/builder.py \
       execution/backend/app/tasks.py execution/backend/app/api/article_batches.py \
       execution/backend/tests/test_models_article.py execution/backend/tests/test_articles_builder.py \
       execution/backend/tests/test_tasks.py execution/backend/tests/test_api_batches.py \
       execution/frontend/src/api.ts execution/frontend/src/pages/BatchPage.tsx
git commit -m "refactor: переименовать images_regenerating в regenerating"
```

---

### Task 2: `SiteClient.update_page_text` — необязательные title/meta

**Files:**
- Modify: `execution/backend/app/sites/client.py:170-182`
- Test: `execution/backend/tests/test_sites_client.py`

- [ ] **Step 1: Написать падающие тесты**

В `execution/backend/tests/test_sites_client.py`, после `test_upload_file_builds_predictable_path` (или в конец файла), добавить:

```python
def test_update_page_text_sends_only_text_by_default(monkeypatch):
    captured = {}

    def fake_patch(url, **kwargs):
        captured.update(url=url, json=kwargs["json"])
        return FakeResponse(200, {"id": 501})

    monkeypatch.setattr("app.sites.client.requests.patch", fake_patch)
    SiteClient("https://x.ru", "token").update_page_text(501, "<p>текст</p>")
    assert captured["json"] == {"text": "<p>текст</p>"}


def test_update_page_text_includes_title_and_meta_when_given(monkeypatch):
    captured = {}

    def fake_patch(url, **kwargs):
        captured.update(json=kwargs["json"])
        return FakeResponse(200, {"id": 501})

    monkeypatch.setattr("app.sites.client.requests.patch", fake_patch)
    SiteClient("https://x.ru", "token").update_page_text(
        501, "<p>новый текст</p>", title="Новый заголовок",
        meta_description="новое описание", meta_keywords="новые, ключи")
    assert captured["json"] == {
        "text": "<p>новый текст</p>",
        "title": "Новый заголовок",
        "meta_description": "новое описание",
        "meta_keywords": "новые, ключи",
    }
```

- [ ] **Step 2: Проверить, что второй тест падает**

Run: `docker compose run --rm --no-deps backend pytest tests/test_sites_client.py -k update_page_text -v`
Expected: FAIL — `update_page_text() got an unexpected keyword argument 'title'`.

- [ ] **Step 3: Реализовать**

В `execution/backend/app/sites/client.py` заменить:

```python
    def update_page_text(self, page_id: int, html: str) -> dict:
        """PATCH тела уже существующей страницы. Используется вне обычного
        потока сборки (build_for создаёт страницу один раз и больше не
        трогает) — для ручного исправления уже опубликованного контента,
        например замены путей картинок после коллизии имён в filemanager."""
        payload = {"text": strip_html_comments(html).strip()}
        response = self._check(
            requests.patch(f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
                           json=payload,
                           headers={**self._headers, "Content-Type": "application/json"},
                           timeout=self.timeout),
            f"обновление страницы {page_id}")
        return self._json(response, f"обновление страницы {page_id}")
```

на:

```python
    def update_page_text(self, page_id: int, html: str, *, title: str | None = None,
                         meta_description: str | None = None,
                         meta_keywords: str | None = None) -> dict:
        """PATCH тела уже существующей страницы. Используется вне обычного
        потока сборки (build_for создаёт страницу один раз и больше не
        трогает) — для ручного исправления уже опубликованного контента
        (замена путей картинок после коллизии имён в filemanager) и для
        перегенерации текста статьи (ArticleBuilder.regenerate_text,
        directions/2026-09-09-article-full-regeneration-design.md).

        title/meta_description/meta_keywords — именованные и необязательные:
        существующие вызывающие (fix_article_image_collision.py,
        app/companies/builder.py, ArticleBuilder.regenerate_content_images)
        зовут метод с ровно двумя позиционными аргументами и продолжают
        менять только текст. regenerate_text — единственный вызывающий,
        которому нужны все четыре поля в одном PATCH: заголовок, meta и
        текст обновляются на сайте атомарно, а не рассинхронизированной
        парой запросов."""
        payload = {"text": strip_html_comments(html).strip()}
        if title is not None:
            payload["title"] = title
        if meta_description is not None:
            payload["meta_description"] = meta_description
        if meta_keywords is not None:
            payload["meta_keywords"] = meta_keywords
        response = self._check(
            requests.patch(f"{self.base_url}{STATICPAGES_PATH}{page_id}/",
                           json=payload,
                           headers={**self._headers, "Content-Type": "application/json"},
                           timeout=self.timeout),
            f"обновление страницы {page_id}")
        return self._json(response, f"обновление страницы {page_id}")
```

- [ ] **Step 4: Запустить тесты**

Run: `docker compose run --rm --no-deps backend pytest tests/test_sites_client.py -v`
Expected: все тесты файла PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/sites/client.py execution/backend/tests/test_sites_client.py
git commit -m "feat: SiteClient.update_page_text — необязательные title/meta"
```

---

### Task 3: `ArticleBuilder._generate_body` — переиспользуемые пути картинок

**Files:**
- Modify: `execution/backend/app/articles/builder.py:288-325`
- Test: `execution/backend/tests/test_articles_builder.py`

- [ ] **Step 1: Написать падающие тесты**

В `execution/backend/tests/test_articles_builder.py`, после блока crop-тестов (после `test_regenerate_content_images_uses_measured_ratio_for_position`, перед `def test_build_sets_title_slug_and_html`), добавить:

```python
# --- _generate_body умеет принимать готовый список путей картинок вместо
# построения его с нуля — нужно regenerate_text() (Task 4): у уже
# опубликованной статьи актуальные пути могут быть НЕ v1 (если картинки уже
# перегенерировались), а image_paths_for()/_image_count() всегда строят
# именно v1-заглушки, актуальные только при самой первой сборке. ---


def test_generate_body_uses_provided_image_paths_when_given(db_session, prepared):
    builder = make_builder(db_session, prepared)
    custom_paths = ["/media/uploads/article-img/cp-article-1-1_v2.webp"]
    builder._generate_body(image_paths=custom_paths)
    prompt = builder.text_client.prompts[0]
    assert custom_paths[0] in prompt
    assert "ровно 1 иллюстраций" in prompt


def test_generate_body_falls_back_to_reference_count_without_image_paths(db_session, prepared):
    builder = make_builder(db_session, prepared)
    builder._generate_body()
    prompt = builder.text_client.prompts[0]
    assert "ровно 2 иллюстраций" in prompt   # prepared.site.reference_images == 2
    assert image_paths_for(prepared.article.id, 2)[0] in prompt


def test_current_content_image_paths_returns_latest_version_per_position(db_session, prepared):
    from app.models.article import ArticleImage

    db_session.add_all([
        ArticleImage(article_id=prepared.article.id, kind="content", position=1, version=1,
                    remote_path="/media/x/cp-article-1-1.webp"),
        ArticleImage(article_id=prepared.article.id, kind="content", position=1, version=2,
                    remote_path="/media/x/cp-article-1-1_v2.webp"),
        ArticleImage(article_id=prepared.article.id, kind="content", position=2, version=1,
                    remote_path="/media/x/cp-article-1-2.webp"),
    ])
    db_session.commit()
    builder = make_builder(db_session, prepared)
    assert builder._current_content_image_paths() == [
        "/media/x/cp-article-1-1_v2.webp", "/media/x/cp-article-1-2.webp",
    ]


def test_current_content_image_paths_empty_when_no_images(db_session, prepared):
    builder = make_builder(db_session, prepared)
    assert builder._current_content_image_paths() == []
```

- [ ] **Step 2: Проверить, что тесты падают**

Run: `docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -k "generate_body_uses_provided or current_content_image_paths" -v`
Expected: FAIL — `_generate_body() got an unexpected keyword argument 'image_paths'` и `'ArticleBuilder' object has no attribute '_current_content_image_paths'`.

- [ ] **Step 3: Реализовать**

В `execution/backend/app/articles/builder.py` заменить сигнатуру и первые две строки `_generate_body`:

```python
    def _generate_body(self, image_paths: list[str] | None = None) -> dict:
```

и

```python
        count = self._image_count()
        template = resolve_prompt(self.db, "article_body", self.site.id)
        prompt = render_prompt(template, {
            "topic": self.article.topic,
            "site_name": self.site.name,
            "site_description": self.site.site_description,
            "tone_of_voice": self.site.tone_of_voice,
            # Эталон берётся из кеша карточки — к сайту за ним не ходим.
            "reference_html": self.site.reference_html,
            "image_count": count,
            "image_paths": image_paths_for(self.article.id, count),
        })
```

на:

```python
        # image_paths передаётся явно при перегенерации текста уже
        # опубликованной статьи (regenerate_text) — актуальные пути картинок
        # могут быть НЕ v1, если картинки уже перегенерировались хотя бы раз
        # (_current_content_image_paths ниже). Без параметра (обычная первая
        # сборка, build()) считаем как раньше: image_paths_for() всегда
        # строит v1 — картинок ещё не существует, они будут созданы именно
        # под эти пути следующим шагом (_generate_content_images).
        count = self._image_count() if image_paths is None else len(image_paths)
        paths = image_paths_for(self.article.id, count) if image_paths is None else image_paths
        template = resolve_prompt(self.db, "article_body", self.site.id)
        prompt = render_prompt(template, {
            "topic": self.article.topic,
            "site_name": self.site.name,
            "site_description": self.site.site_description,
            "tone_of_voice": self.site.tone_of_voice,
            # Эталон берётся из кеша карточки — к сайту за ним не ходим.
            "reference_html": self.site.reference_html,
            "image_count": count,
            "image_paths": paths,
        })
```

Оставить без изменений остаток метода (`result = self.text_client.complete_json(prompt)` и далее) — сигнатура `_generate_body(self)` в `build()` (`body = self._generate_body()`) не меняется, `image_paths=None` даёт прежнее поведение.

Добавить новый приватный метод сразу после `_image_count`:

```python
    def _current_content_image_paths(self) -> list[str]:
        """Текущие (последней версии на позицию) пути контентных картинок —
        нужны regenerate_text(): новый body_html должен продолжать ссылаться
        на реально существующие файлы, а не на v1-заглушки image_paths_for(),
        актуальные только при самой первой сборке статьи. Пустой список,
        если у статьи вообще нет контентных картинок — не должно ронять
        текст, только оставить его без упоминания иллюстраций."""
        images = self.db.query(ArticleImage).filter_by(
            article_id=self.article.id, kind="content").all()
        positions = sorted({i.position for i in images})
        return [
            max((i for i in images if i.position == p), key=lambda i: i.version).remote_path
            for p in positions
        ]
```

- [ ] **Step 4: Запустить тесты**

Run: `docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -v`
Expected: все тесты файла PASS, включая новые и уже существующие (`test_build_sets_title_slug_and_html` и другие, использующие `_generate_body()` косвенно через `build()`, не должны измениться).

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/articles/builder.py execution/backend/tests/test_articles_builder.py
git commit -m "feat: _generate_body принимает готовый список путей картинок"
```

---

### Task 4: `ArticleBuilder.regenerate_text()`

**Files:**
- Modify: `execution/backend/app/articles/builder.py`
- Test: `execution/backend/tests/test_articles_builder.py`

- [ ] **Step 1: Обновить `FakeSiteClient.update_page_text` под новую сигнатуру**

В `execution/backend/tests/test_articles_builder.py`, класс `FakeSiteClient`, заменить:

```python
    def update_page_text(self, page_id, html):
        self.updated_text = (page_id, html)
        return {"id": page_id}
```

на:

```python
    def update_page_text(self, page_id, html, *, title=None, meta_description=None,
                         meta_keywords=None):
        self.updated_text = (page_id, html, title, meta_description, meta_keywords)
        return {"id": page_id}
```

Обновить единственную существующую точную проверку кортежа — найти строку
`assert site_client.updated_text == (501, prepared.article.body_html)` (в
`test_regenerate_content_images_uploads_versioned_files_and_updates_body`) и
заменить на:

```python
    assert site_client.updated_text == (501, prepared.article.body_html, None, None, None)
```

(`regenerate_content_images` не передаёт title/meta — они остаются `None`.)

- [ ] **Step 2: Написать падающие тесты**

В конец файла (после последнего теста `regenerate_content_images`) добавить:

```python
# --- Перегенерация текста уже опубликованной статьи ---


def test_regenerate_text_updates_title_body_meta_without_changing_slug(db_session, prepared):
    site_client = FakeSiteClient()
    body = {
        "title": "Чем утеплить каркасный дом", "html": "<p>первая версия</p>",
        "meta_description": "старое описание", "meta_keywords": "старое",
    }
    builder = make_builder(db_session, prepared, site_client, body=body)
    builder.build()
    old_slug = prepared.article.slug
    assert old_slug == "chem-uteplit-karkasnyy-dom"

    prepared.article.regenerating = True
    db_session.commit()
    new_body = {
        "title": "Совсем другой заголовок", "html": "<p>новая версия текста</p>",
        "meta_description": "новое описание", "meta_keywords": "новое",
    }
    builder.text_client = FakeTextClient(new_body)
    builder.regenerate_text()

    assert prepared.article.title == "Совсем другой заголовок"
    assert prepared.article.body_html == "<p>новая версия текста</p>"
    assert prepared.article.meta_description == "новое описание"
    assert prepared.article.meta_keywords == "новое"
    assert prepared.article.slug == old_slug   # URL закреплён — не пересчитывается
    assert prepared.article.regenerating is False
    assert prepared.article.error_text == ""
    assert site_client.updated_text == (
        501, "<p>новая версия текста</p>", "Совсем другой заголовок",
        "новое описание", "новое")


def test_regenerate_text_uses_current_not_v1_image_paths(db_session, prepared):
    """Если картинки уже перегенерировались (сейчас v2), свежий текст обязан
    сослаться на v2, а не откатить разметку на v1 (§1 дизайн-документа
    2026-09-09-article-full-regeneration)."""
    from app.models.article import ArticleImage

    builder = make_builder(db_session, prepared)
    db_session.add(ArticleImage(
        article_id=prepared.article.id, kind="content", position=1, version=2,
        remote_path="/media/uploads/article-img/cp-article-1-1_v2.webp"))
    db_session.commit()

    builder.regenerate_text()

    prompt = builder.text_client.prompts[0]
    assert "/media/uploads/article-img/cp-article-1-1_v2.webp" in prompt


def test_regenerate_text_failure_does_not_touch_existing_text(db_session, prepared):
    builder = make_builder(db_session, prepared)
    builder.build()
    old_title = prepared.article.title
    old_body = prepared.article.body_html

    def broken_json(prompt):
        from app.ai.text import LLMError
        raise LLMError("модель вернула не JSON: извините")

    builder.text_client.complete_json = broken_json
    builder.regenerate_text()

    assert prepared.article.title == old_title
    assert prepared.article.body_html == old_body
    assert "не JSON" in prepared.article.error_text
    assert prepared.article.regenerating is False


def test_regenerate_text_site_push_failure_is_reported(db_session, prepared):
    class BrokenPushClient(FakeSiteClient):
        def update_page_text(self, page_id, html, **kwargs):
            from app.sites.client import SiteAPIError
            raise SiteAPIError("обновление страницы: HTTP 500: сорвался сайт")

    builder = make_builder(db_session, prepared, BrokenPushClient())
    builder.build()
    builder.regenerate_text()

    assert "сорвался сайт" in prepared.article.error_text
    assert prepared.article.regenerating is False


def test_regenerate_text_requires_synced_reference(db_session, prepared):
    prepared.site.reference_html = ""
    prepared.site.reference_images = 0
    db_session.commit()
    builder = make_builder(db_session, prepared)

    builder.regenerate_text()

    assert "синхронизирован" in prepared.article.error_text
    assert prepared.article.regenerating is False
```

- [ ] **Step 3: Проверить, что тесты падают**

Run: `docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -k regenerate_text -v`
Expected: FAIL — `AttributeError: 'ArticleBuilder' object has no attribute 'regenerate_text'`.

- [ ] **Step 4: Реализовать**

В `execution/backend/app/articles/builder.py`, добавить новый публичный метод в класс `ArticleBuilder` сразу после `regenerate_content_images()` (перед строкой `# --- шаги ---`):

```python
    def regenerate_text(self) -> None:
        """Перегенерирует заголовок (только отображаемый), текст и meta уже
        опубликованной статьи. slug/URL НЕ пересчитывается — в отличие от
        _apply_body() (используется только при первой публикации), здесь
        нет строки `self.article.slug = slugify(...)`: у API сайта нет
        метода переименования уже созданной страницы (directions/2026-09-09-
        article-full-regeneration-design.md, §4), и самостоятельный пересчёт
        articles_url_prefix + slugify(new_title) без факта переименования на
        сайте рассинхронизировал бы article.remote_url с реальным адресом.

        Не трогает картинки (ни контентные, ни обложку) — новый body_html
        продолжает ссылаться на текущие файлы (_current_content_image_paths),
        а не на v1-заглушки, актуальные только для самой первой сборки."""
        try:
            self._require_synced_reference()
            body = self._generate_body(image_paths=self._current_content_image_paths())
            self.article.title = body.get("title") or self.article.topic
            self.article.body_html = body["html"]
            self.article.meta_description = body.get("meta_description", "")
            self.article.meta_keywords = body.get("meta_keywords", "")
            self.db.commit()
            self.site_client.update_page_text(
                self.article.remote_page_id, self.article.body_html,
                title=self.article.title, meta_description=self.article.meta_description,
                meta_keywords=self.article.meta_keywords)
        except (LLMError, PromptError, SiteAPIError) as exc:
            self.db.rollback()
            self.article.error_text = str(exc)
            self.article.regenerating = False
            self.db.commit()
            return
        self.article.error_text = ""
        self.article.regenerating = False
        self.db.commit()
```

- [ ] **Step 5: Запустить тесты**

Run: `docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -v`
Expected: все тесты файла PASS, включая пять новых и уже существующие (с обновлённым `updated_text` кортежем из Step 1).

- [ ] **Step 6: Commit**

```bash
git add execution/backend/app/articles/builder.py execution/backend/tests/test_articles_builder.py
git commit -m "feat: ArticleBuilder.regenerate_text — перегенерация текста опубликованной статьи"
```

---

### Task 5: `ArticleBuilder.regenerate_cover()`

**Files:**
- Modify: `execution/backend/app/articles/builder.py`
- Test: `execution/backend/tests/test_articles_builder.py`

- [ ] **Step 1: Написать падающие тесты**

В конец файла добавить:

```python
# --- Перегенерация обложки уже опубликованной статьи ---


def test_regenerate_cover_uploads_new_version_and_keeps_history(db_session, prepared):
    from app.models.article import ArticleImage

    site_client = FakeSiteClient()
    builder = make_builder(db_session, prepared, site_client)
    builder.build()
    assert site_client.cover == (501, "cp-article-1-cover.webp")

    prepared.article.regenerating = True
    db_session.commit()
    builder.regenerate_cover()

    assert site_client.cover == (501, "cp-article-1-cover_v2.webp")
    covers = db_session.query(ArticleImage).filter_by(
        article_id=prepared.article.id, kind="cover").order_by(ArticleImage.version).all()
    assert [(c.version, c.remote_path) for c in covers] == [
        (1, "cp-article-1-cover.webp"), (2, "cp-article-1-cover_v2.webp"),
    ]
    assert prepared.article.regenerating is False
    assert prepared.article.error_text == ""


def test_regenerate_cover_does_not_touch_content_images_or_body(db_session, prepared):
    builder = make_builder(db_session, prepared)
    builder.build()
    old_body = prepared.article.body_html

    builder.regenerate_cover()

    assert prepared.article.body_html == old_body


def test_regenerate_cover_failure_clears_flag_and_reports_error(db_session, prepared):
    builder = make_builder(db_session, prepared)
    builder.build()

    class BrokenCoverClient(FakeSiteClient):
        def set_page_cover(self, page_id, image_bytes, filename):
            from app.sites.client import SiteAPIError
            raise SiteAPIError("загрузка обложки: HTTP 500: сорвался сайт")

    builder.site_client = BrokenCoverClient()
    prepared.article.regenerating = True
    db_session.commit()

    builder.regenerate_cover()

    assert "сорвался сайт" in prepared.article.error_text
    assert prepared.article.regenerating is False
```

- [ ] **Step 2: Проверить, что тесты падают**

Run: `docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -k regenerate_cover -v`
Expected: FAIL — `AttributeError: 'ArticleBuilder' object has no attribute 'regenerate_cover'`.

- [ ] **Step 3: Реализовать**

В `execution/backend/app/articles/builder.py`, добавить новый метод сразу после `regenerate_text()`:

```python
    def regenerate_cover(self) -> None:
        """Перегенерирует обложку страницы (teaser_image) уже опубликованной
        статьи. В отличие от контентных картинок обложка не встроена в
        body_html — сайт хранит её как одно поле страницы, которое
        set_page_cover просто перезаписывает. Версия в имени файла нужна
        только для истории/аудита стоимости в ArticleImage, а не чтобы
        сохранить старую обложку видимой где-то ещё — она и так перестаёт
        быть видимой на сайте в момент замены поля, независимо от имени
        файла (в отличие от контентных картинок, которых может быть
        несколько одновременно видимых в теле статьи — там версия в имени
        нужна, чтобы не перезаписать файл, который всё ещё показан)."""
        try:
            style = (self.site.cover_style_prompt if self.site.cover_mode == "prompt"
                     else "в стиле уже существующих обложек этого сайта")
            prompt = self._image_prompt("cover", {"topic": self.article.topic,
                                                  "cover_style": style})
            result = self.image_generator.generate(
                prompt=prompt, size=self.image_params["size"],
                quality=self.image_params["quality"], crop=COVER_CROP)
            covers = self.db.query(ArticleImage).filter_by(
                article_id=self.article.id, kind="cover").all()
            next_version = max((c.version for c in covers), default=0) + 1
            filename = image_filename(self.article.id, 0, version=next_version)
            self.site_client.set_page_cover(self.article.remote_page_id, result.data, filename)
            self.db.add(ArticleImage(article_id=self.article.id, kind="cover", position=0,
                                     version=next_version, prompt=prompt,
                                     remote_path=filename, cost=result.cost))
            self._record_usage("image", 0, 0, result.cost)
        except (LLMError, ImageError, SiteAPIError, PromptError) as exc:
            self.db.rollback()
            self.article.error_text = f"ошибка: {exc}"
            self.article.regenerating = False
            self.db.commit()
            return
        self.article.error_text = ""
        self.article.regenerating = False
        self.db.commit()
```

- [ ] **Step 4: Запустить тесты**

Run: `docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -v`
Expected: все тесты файла PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/articles/builder.py execution/backend/tests/test_articles_builder.py
git commit -m "feat: ArticleBuilder.regenerate_cover — перегенерация обложки опубликованной статьи"
```

---

### Task 6: `ArticleBuilder.regenerate()` — оркестратор + обобщённая Celery-задача

Задача объединяет то, что в отдельности не проходило бы «зелёным»
промежуточным состоянием: `regenerate_images_for` в `builder.py`
переименовывается в `regenerate_article_for`, и `app/tasks.py` импортирует
именно её — если бы это были две разные задачи (сначала переименование в
builder.py, потом использование в tasks.py), между ними `app/tasks.py` (а с
ним и `app/api/article_batches.py`, и весь FastAPI-app, который его
импортирует) был бы битым `ImportError` до второй задачи. Поэтому здесь оба
файла меняются одним коммитом, и в конце запускается ПОЛНЫЙ набор тестов, а
не только `test_articles_builder.py`/`test_tasks.py` по отдельности.

**Files:**
- Modify: `execution/backend/app/articles/builder.py`
- Modify: `execution/backend/app/tasks.py`
- Test: `execution/backend/tests/test_articles_builder.py`
- Test: `execution/backend/tests/test_tasks.py`

#### Часть A: `ArticleBuilder.regenerate()`

- [ ] **Step 1: Написать падающие тесты**

В конец файла добавить:

```python
# --- Оркестратор: одна Celery-задача, любая комбинация text/images/cover ---


def test_regenerate_runs_text_before_images(db_session, prepared):
    """Порядок фиксирован (§1 дизайн-документа): если бы картинки
    обрабатывались первыми, замена путей ушла бы в СТАРЫЙ body_html и была
    бы перезаписана следующим шагом текста."""
    builder = make_builder(db_session, prepared)
    builder.build()

    calls_order = []
    original_text = builder.regenerate_text
    original_images = builder.regenerate_content_images
    builder.regenerate_text = lambda: (calls_order.append("text"), original_text())[1]
    builder.regenerate_content_images = \
        lambda: (calls_order.append("images"), original_images())[1]

    builder.regenerate(text=True, images=True, cover=False)

    assert calls_order == ["text", "images"]


def test_regenerate_only_calls_selected_parts(db_session, prepared):
    builder = make_builder(db_session, prepared)
    builder.build()

    calls = []
    builder.regenerate_text = lambda: calls.append("text")
    builder.regenerate_content_images = lambda: calls.append("images")
    builder.regenerate_cover = lambda: calls.append("cover")

    builder.regenerate(text=False, images=True, cover=False)

    assert calls == ["images"]
    assert prepared.article.regenerating is False


def test_regenerate_aggregates_errors_from_each_failed_part(db_session, prepared):
    builder = make_builder(db_session, prepared)
    builder.build()

    def broken_json(prompt):
        from app.ai.text import LLMError
        raise LLMError("сорвался текст")

    builder.text_client.complete_json = broken_json

    class BrokenCoverClient(FakeSiteClient):
        def set_page_cover(self, page_id, image_bytes, filename):
            from app.sites.client import SiteAPIError
            raise SiteAPIError("сорвалась обложка")

    builder.site_client = BrokenCoverClient()

    builder.regenerate(text=True, images=False, cover=True)

    assert "текст" in prepared.article.error_text
    assert "сорвался текст" in prepared.article.error_text
    assert "обложка" in prepared.article.error_text
    assert "сорвалась обложка" in prepared.article.error_text
    assert prepared.article.regenerating is False


def test_regenerate_with_no_parts_selected_records_error(db_session, prepared):
    builder = make_builder(db_session, prepared)
    builder.build()
    prepared.article.regenerating = True
    db_session.commit()

    builder.regenerate(text=False, images=False, cover=False)

    assert "не выбрана" in prepared.article.error_text
    assert prepared.article.regenerating is False
```

- [ ] **Step 2: Проверить, что тесты падают**

Run: `docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -k "test_regenerate_runs_text_before_images or test_regenerate_only_calls_selected_parts or test_regenerate_aggregates_errors or test_regenerate_with_no_parts_selected" -v`
Expected: FAIL — `AttributeError: 'ArticleBuilder' object has no attribute 'regenerate'` (не путать с уже существующим `regenerate_content_images`/`regenerate_text`/`regenerate_cover` — метод называется ровно `regenerate`, без суффикса).

- [ ] **Step 3: Реализовать**

В `execution/backend/app/articles/builder.py`, добавить метод сразу после `regenerate_cover()`:

```python
    def regenerate(self, *, text: bool, images: bool, cover: bool) -> None:
        """Единая точка входа для кнопок «Перегенерировать» — один клик
        может попросить любую комбинацию из трёх частей. Порядок фиксирован:
        текст всегда первым (§1 directions/2026-09-09-article-full-
        regeneration-design.md) — если картинки тоже выбраны,
        regenerate_content_images() должен искать свои старые пути в СВЕЖЕМ
        body_html, а не в том, что было до regenerate_text(). Обложка не
        встроена в body_html, порядка с остальными частями не имеет.

        Каждая часть уже сама коммитит и очищает regenerating — здесь
        читаем article.error_text сразу после каждого шага (пока его не
        перезаписал следующий) и в конце делаем один комбинированный
        commit. Одна упавшая часть не останавливает остальные — тот же
        принцип, что уже применяется для отдельных картинок внутри
        regenerate_content_images."""
        if not (text or images or cover):
            self.article.error_text = "нечего перегенерировать — не выбрана ни одна часть"
            self.article.regenerating = False
            self.db.commit()
            return

        errors: list[str] = []
        if text:
            self.regenerate_text()
            if self.article.error_text:
                errors.append(f"текст — {self.article.error_text}")
        if images:
            self.regenerate_content_images()
            if self.article.error_text:
                errors.append(f"картинки — {self.article.error_text}")
        if cover:
            self.regenerate_cover()
            if self.article.error_text:
                errors.append(f"обложка — {self.article.error_text}")

        self.article.error_text = "; ".join(errors)
        self.article.regenerating = False
        self.db.commit()
```

Переименовать функцию-обёртку в конце файла: заменить

```python
def regenerate_images_for(db: Session, article: Article, site: Site, site_client,
                          job_run_id: int | None) -> None:
    """Как build_for выше, но перегенерирует только контентные картинки уже
    опубликованной статьи — не создаёт страницу заново и не трогает
    обложку. Тот же открытый риск с порядком AIConfigError, что
    задокументирован в build_for."""
    watermark = b""
    if site.watermark_path:
        try:
            with open(site.watermark_path, "rb") as f:
                watermark = f.read()
        except OSError:
            watermark = b""

    ArticleBuilder(
        db=db, article=article, site=site,
        text_client=build_text_client(db),
        image_generator=build_image_generator(db),
        site_client=site_client,
        image_params=image_params(db),
        watermark_bytes=watermark,
        job_run_id=job_run_id,
    ).regenerate_content_images()
```

на:

```python
def regenerate_article_for(db: Session, article: Article, site: Site, site_client,
                           job_run_id: int | None, *, text: bool, images: bool,
                           cover: bool) -> None:
    """Как build_for выше, но перегенерирует выбранные части (text/images/
    cover) уже опубликованной статьи — не создаёт страницу заново. Тот же
    открытый риск с порядком AIConfigError, что задокументирован в
    build_for. Не имеет собственного unit-теста (как и build_for — чистая
    сборка зависимостей без ветвлений); логика самого раунда перегенерации
    уже покрыта тестами regenerate_text/regenerate_content_images/
    regenerate_cover/regenerate выше."""
    watermark = b""
    if site.watermark_path:
        try:
            with open(site.watermark_path, "rb") as f:
                watermark = f.read()
        except OSError:
            watermark = b""

    ArticleBuilder(
        db=db, article=article, site=site,
        text_client=build_text_client(db),
        image_generator=build_image_generator(db),
        site_client=site_client,
        image_params=image_params(db),
        watermark_bytes=watermark,
        job_run_id=job_run_id,
    ).regenerate(text=text, images=images, cover=cover)
```

- [ ] **Step 4: Запустить тесты части A**

Run: `docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -v`
Expected: все тесты файла PASS.

#### Часть B: `app/tasks.py` — обобщённая Celery-задача

- [ ] **Step 5: Написать падающие тесты**

В `execution/backend/tests/test_tasks.py` найти блок `# --- перегенерация картинок опубликованной статьи ---` (после Task 1 переименования там уже `regenerating=True` вместо `images_regenerating=True`) и заменить весь блок из 4 тестов (`test_regenerate_article_images_sync_*`) на:

```python
# --- перегенерация опубликованной статьи (текст/картинки/обложка) ---


def test_regenerate_article_sync_calls_builder_and_finishes_job(
        db_session, batch, site, monkeypatch):
    from app.tasks import regenerate_article_sync

    article = Article(batch_id=batch.id, site_id=site.id, topic="Тема",
                      status="published", remote_page_id=501,
                      regenerating=True)
    db_session.add(article)
    db_session.commit()

    calls = []
    monkeypatch.setattr(
        "app.tasks.regenerate_article_for",
        lambda db, a, s, sc, job_id, **kwargs: calls.append((a.id, s.id, job_id, kwargs)))
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: SimpleNamespace())

    regenerate_article_sync(db_session, article.id, text=True, images=False, cover=True)
    db_session.refresh(article)

    assert len(calls) == 1
    assert calls[0][0] == article.id and calls[0][1] == site.id
    assert calls[0][3] == {"text": True, "images": False, "cover": True}
    assert article.regenerating is False


def test_regenerate_article_sync_skips_non_published_article(
        db_session, batch, site, monkeypatch):
    from app.tasks import regenerate_article_sync

    article = Article(batch_id=batch.id, site_id=site.id, topic="Тема",
                      status="draft", regenerating=True)
    db_session.add(article)
    db_session.commit()

    calls = []
    monkeypatch.setattr("app.tasks.regenerate_article_for", lambda *a, **k: calls.append(1))

    regenerate_article_sync(db_session, article.id, text=False, images=True, cover=False)
    db_session.refresh(article)

    assert calls == []
    assert article.regenerating is False


def test_regenerate_article_sync_ai_config_error_marks_failed(
        db_session, batch, site, monkeypatch):
    from app.ai.factory import AIConfigError
    from app.tasks import regenerate_article_sync

    article = Article(batch_id=batch.id, site_id=site.id, topic="Тема",
                      status="published", remote_page_id=501,
                      regenerating=True)
    db_session.add(article)
    db_session.commit()

    def broken(db, article, site, site_client, job_run_id, **kwargs):
        raise AIConfigError("ключ RouterAI не задан — заполните routerai_api_key")

    monkeypatch.setattr("app.tasks.regenerate_article_for", broken)
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: SimpleNamespace())

    regenerate_article_sync(db_session, article.id, text=False, images=True, cover=False)
    db_session.refresh(article)

    assert article.regenerating is False
    assert "ключ" in article.error_text
    assert article.status == "published"   # перегенерация не трогает статус статьи


def test_regenerate_article_sync_without_site_marks_failed(db_session, admin):
    from app.tasks import regenerate_article_sync

    orphan_batch = ArticleBatch(site_id=None, requested_count=1, created_by_id=admin.id)
    db_session.add(orphan_batch)
    db_session.commit()
    article = Article(batch_id=orphan_batch.id, site_id=None, topic="Тема",
                      status="published", remote_page_id=501,
                      regenerating=True)
    db_session.add(article)
    db_session.commit()

    regenerate_article_sync(db_session, article.id, text=False, images=True, cover=False)
    db_session.refresh(article)

    assert article.regenerating is False
    assert "удал" in article.error_text
```

- [ ] **Step 6: Проверить, что тесты падают**

Run: `docker compose run --rm --no-deps backend pytest tests/test_tasks.py -k regenerate_article_sync -v`
Expected: FAIL — `ImportError: cannot import name 'regenerate_article_sync' from 'app.tasks'`.

- [ ] **Step 7: Реализовать**

В `execution/backend/app/tasks.py`, изменить импорт:

```python
from app.articles.builder import build_for, regenerate_article_for
```

Заменить весь блок (после Task 1 переименования там уже `article.regenerating`):

```python
# --- перегенерация картинок опубликованной статьи ---

def regenerate_article_images_sync(db, article_id: int) -> None:
    """В отличие от retry_article_sync, ни одна ветка здесь НЕ трогает
    article.status — картинки перегенерируются у уже опубликованной статьи,
    её страница на сайте продолжает существовать и работать независимо от
    исхода этого раунда. Отказ отражается только в regenerating/
    error_text. Это намеренное расхождение с соседней retry_article_sync
    (которая как раз обязана переводить статью в "failed"), а не пропуск —
    не «чинить» по аналогии с ней."""
    article = db.get(Article, article_id)
    if article.status != "published":
        article.regenerating = False
        db.commit()
        return

    site = db.get(Site, article.site_id) if article.site_id is not None else None
    if site is None:
        article.regenerating = False
        article.error_text = "сайт этой статьи удалён — перегенерация картинок невозможна"
        db.commit()
        job = _start_job(db, "regenerate_article_images", None, None,
                         {"article_id": article_id})
        _finish_job(db, job, "failed", article.error_text)
        return

    job = _start_job(db, "regenerate_article_images", site.id, None,
                     {"article_id": article_id})
    try:
        regenerate_images_for(db, article, site, open_site_client(db, site), job.id)
    except SoftTimeLimitExceeded:
        article.regenerating = False
        article.error_text = "превышен лимит времени задачи"
        db.commit()
        _finish_job(db, job, "failed", article.error_text)
        return
    except (AIConfigError, SecretDecryptionError) as exc:
        article.regenerating = False
        article.error_text = str(exc)
        db.commit()
        _finish_job(db, job, "failed", str(exc))
        return

    article.regenerating = False
    db.commit()
    _finish_job(db, job, "ok" if not article.error_text else "failed", article.error_text)


@celery_app.task(name="app.tasks.regenerate_article_images")
def regenerate_article_images(article_id: int) -> None:
    db = SessionLocal()
    try:
        regenerate_article_images_sync(db, article_id)
    finally:
        db.close()
```

на:

```python
# --- перегенерация опубликованной статьи (текст/картинки/обложка) ---

def regenerate_article_sync(db, article_id: int, *, text: bool, images: bool,
                            cover: bool) -> None:
    """В отличие от retry_article_sync, ни одна ветка здесь НЕ трогает
    article.status — статья уже опубликована, её страница на сайте
    продолжает существовать и работать независимо от исхода этого раунда.
    Отказ отражается только в regenerating/error_text. Это намеренное
    расхождение с соседней retry_article_sync (которая как раз обязана
    переводить статью в "failed"), а не пропуск — не «чинить» по аналогии
    с ней."""
    article = db.get(Article, article_id)
    if article.status != "published":
        # Гонка с эндпоинтом (app/api/article_batches.py, regenerate): он
        # уже отклоняет неопубликованные статьи синхронно, сюда можно
        # попасть только если статус успел измениться между постановкой
        # задачи и её реальным стартом. Тихий выход, тот же стиль, что и у
        # generate_topics_sync при повторной постановке той же задачи.
        article.regenerating = False
        db.commit()
        return

    site = db.get(Site, article.site_id) if article.site_id is not None else None
    if site is None:
        article.regenerating = False
        article.error_text = "сайт этой статьи удалён — перегенерация невозможна"
        db.commit()
        job = _start_job(db, "regenerate_article", None, None, {"article_id": article_id})
        _finish_job(db, job, "failed", article.error_text)
        return

    job = _start_job(db, "regenerate_article", site.id, None, {"article_id": article_id})
    try:
        regenerate_article_for(db, article, site, open_site_client(db, site), job.id,
                               text=text, images=images, cover=cover)
    except SoftTimeLimitExceeded:
        article.regenerating = False
        article.error_text = "превышен лимит времени задачи"
        db.commit()
        _finish_job(db, job, "failed", article.error_text)
        return
    except (AIConfigError, SecretDecryptionError) as exc:
        article.regenerating = False
        article.error_text = str(exc)
        db.commit()
        _finish_job(db, job, "failed", str(exc))
        return

    # Подстраховка: ArticleBuilder.regenerate() сама снимает этот флаг по
    # завершении, но обёртка не должна полагаться на то, что он снят именно
    # билдером — иначе тест, подменяющий regenerate_article_for целиком (без
    # реального билдера), не может проверить, что флаг снимается, а сама
    # обёртка перестаёт быть источником истины о собственном состоянии.
    article.regenerating = False
    db.commit()
    _finish_job(db, job, "ok" if not article.error_text else "failed", article.error_text)


@celery_app.task(name="app.tasks.regenerate_article")
def regenerate_article(article_id: int, *, text: bool, images: bool, cover: bool) -> None:
    db = SessionLocal()
    try:
        regenerate_article_sync(db, article_id, text=text, images=images, cover=cover)
    finally:
        db.close()
```

- [ ] **Step 8: Запустить тесты части B**

Run: `docker compose run --rm --no-deps backend pytest tests/test_tasks.py -v`
Expected: все тесты файла PASS.

- [ ] **Step 9: Прогнать весь бэкендный набор тестов**

Run: `docker compose run --rm --no-deps backend pytest -q`
Expected: все тесты проходят. Это первая точка в плане, где `builder.py` и `tasks.py` меняются согласованно — full run подтверждает, что нигде больше не осталось импорта `regenerate_images_for`/`regenerate_article_images_sync`/`regenerate_article_images`.

- [ ] **Step 10: Commit**

Части A и B коммитятся вместе одним коммитом — переименование `regenerate_images_for` → `regenerate_article_for` в `builder.py` и его использование в `tasks.py` должны попасть в историю атомарно, иначе между двумя коммитами `app/tasks.py` (и всё, что его импортирует) был бы битым.

```bash
git add execution/backend/app/articles/builder.py execution/backend/app/tasks.py \
       execution/backend/tests/test_articles_builder.py execution/backend/tests/test_tasks.py
git commit -m "feat: ArticleBuilder.regenerate — оркестратор text/images/cover + Celery-задача"
```

---

### Task 7: API — `POST /api/articles/{id}/regenerate`

**Files:**
- Modify: `execution/backend/app/api/article_batches.py`
- Test: `execution/backend/tests/test_api_batches.py`

- [ ] **Step 1: Обновить фикстуру `no_celery` и переписать тесты регенерации**

В `execution/backend/tests/test_api_batches.py`, в фикстуре `no_celery`, заменить:

```python
    monkeypatch.setattr(
        "app.api.article_batches.regenerate_article_images.apply_async",
        lambda args, **kwargs: sent.append(("regenerate", args[0], kwargs)) or
        type("R", (), {"id": "task-4"})())
```

на:

```python
    monkeypatch.setattr(
        "app.api.article_batches.regenerate_article.apply_async",
        lambda args, **kwargs: sent.append(("regenerate", args[0], kwargs)) or
        type("R", (), {"id": "task-4"})())
```

Найти блок от `def test_regen_time_limits_grow_with_image_count():` до конца файла (`test_batch_detail_includes_regenerating_flag`, уже переименован в Task 1) и заменить целиком на:

```python
def test_regen_time_limits_soft_grows_with_each_part():
    from app.api.article_batches import _regen_time_limits

    none_selected, _ = _regen_time_limits(text=False, image_count=0, cover=False)
    text_only, _ = _regen_time_limits(text=True, image_count=0, cover=False)
    images_only, _ = _regen_time_limits(text=False, image_count=1, cover=False)
    cover_only, _ = _regen_time_limits(text=False, image_count=0, cover=True)
    all_parts, _ = _regen_time_limits(text=True, image_count=1, cover=True)

    assert text_only > none_selected
    assert images_only > none_selected
    assert cover_only > none_selected
    assert all_parts > max(text_only, images_only, cover_only)


def test_regen_time_limits_image_count_scales_soft_limit():
    from app.api.article_batches import _regen_time_limits

    soft_one, _ = _regen_time_limits(text=False, image_count=1, cover=False)
    soft_many, _ = _regen_time_limits(text=False, image_count=5, cover=False)
    assert soft_many > soft_one


def test_regen_time_limits_hard_is_always_after_soft():
    from app.api.article_batches import _regen_time_limits

    soft, hard = _regen_time_limits(text=True, image_count=3, cover=True)
    assert hard > soft


def test_regenerate_unknown_article_404(manager_client, no_celery):
    resp = manager_client.post("/api/articles/999/regenerate", json={"images": True})
    assert resp.status_code == 404


def test_regenerate_requires_at_least_one_part(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501)
    db_session.add(article)
    db_session.commit()

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate",
                               json={"text": False, "images": False, "cover": False})
    assert resp.status_code == 400


def test_regenerate_requires_published_article(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема", status="draft")
    db_session.add(article)
    db_session.commit()

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate", json={"images": True})
    assert resp.status_code == 400


def test_regenerate_starts_task_for_published_article(manager_client, db_session,
                                                       site_id, no_celery):
    from app.models.article import Article, ArticleImage

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501)
    db_session.add(article)
    db_session.commit()
    db_session.add(ArticleImage(article_id=article.id, kind="content", position=1,
                                remote_path="/media/x/cp-article-1-1.webp"))
    db_session.commit()

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate", json={"images": True})

    assert resp.status_code == 200
    assert any(entry[:2] == ("regenerate", article.id) for entry in no_celery)
    db_session.refresh(article)
    assert article.regenerating is True


def test_regenerate_passes_selected_parts_to_task(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501)
    db_session.add(article)
    db_session.commit()

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate",
                               json={"text": True, "images": False, "cover": True})

    assert resp.status_code == 200
    dispatch = next(entry for entry in no_celery if entry[0] == "regenerate")
    assert dispatch[2]["kwargs"] == {"text": True, "images": False, "cover": True}


def test_regenerate_time_limit_counts_distinct_positions_only_when_images_selected(
        manager_client, db_session, site_id, no_celery):
    """Регенерация не удаляет старые ArticleImage — вторая версия той же
    позиции добавляет новую строку, не заменяет старую (app/articles/
    builder.py, regenerate_content_images). Бюджет времени обязан считать
    иллюстрации (уникальные position), а не строки."""
    from app.api.article_batches import _regen_time_limits
    from app.models.article import Article, ArticleImage

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501)
    db_session.add(article)
    db_session.commit()
    db_session.add_all([
        ArticleImage(article_id=article.id, kind="content", position=1, version=1,
                    remote_path="/media/x/cp-article-1-1.webp"),
        ArticleImage(article_id=article.id, kind="content", position=1, version=2,
                    remote_path="/media/x/cp-article-1-1_v2.webp"),
    ])
    db_session.commit()

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate", json={"images": True})

    assert resp.status_code == 200
    dispatch = next(entry for entry in no_celery if entry[0] == "regenerate")
    soft_one, _ = _regen_time_limits(text=False, image_count=1, cover=False)
    assert dispatch[2]["soft_time_limit"] == soft_one


def test_regenerate_twice_dispatches_once(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501)
    db_session.add(article)
    db_session.commit()

    first = manager_client.post(f"/api/articles/{article.id}/regenerate", json={"images": True})
    second = manager_client.post(f"/api/articles/{article.id}/regenerate", json={"images": True})

    assert first.status_code == 200
    assert second.status_code == 400
    dispatches = [entry for entry in no_celery if entry[0] == "regenerate"]
    assert len(dispatches) == 1


def test_batch_detail_includes_regenerating_flag(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501, regenerating=True)
    db_session.add(article)
    db_session.commit()

    body = manager_client.get(f"/api/article-batches/{batch_id}").json()
    assert body["articles"][0]["regenerating"] is True
```

- [ ] **Step 2: Проверить, что тесты падают**

Run: `docker compose run --rm --no-deps backend pytest tests/test_api_batches.py -k regenerate -v`
Expected: FAIL — `AttributeError` на `regenerate_article` в `no_celery`, либо 404 на `/regenerate` (эндпоинт ещё не существует).

- [ ] **Step 3: Реализовать**

В `execution/backend/app/api/article_batches.py`:

Изменить импорт (было `from app.tasks import generate_topics, regenerate_article_images, retry_article, run_batch`):

```python
from app.tasks import generate_topics, regenerate_article, retry_article, run_batch
```

Заменить константы и хелпер лимитов времени (было — единственный параметр `image_count`):

```python
# Перегенерация не пересобирает текст и не создаёт страницу заново —
# бюджет считается только по картинкам: N последовательных текстовых
# промптов иллюстраций (_RETRY_PER_IMAGE_SECONDS каждый) плюс одна
# параллельная пачка генерации самих картинок (365 с, см. app/ai/images.py)
# плюс запас на загрузку файлов и update_page_text.
_REGEN_OVERHEAD_SECONDS = 300
_REGEN_IMAGE_BATCH_SECONDS = 365


def _regen_time_limits(image_count: int) -> tuple[int, int]:
    soft = (_REGEN_OVERHEAD_SECONDS + _RETRY_PER_IMAGE_SECONDS * image_count
           + _REGEN_IMAGE_BATCH_SECONDS)
    return soft, soft + TIME_LIMIT_GAP_SECONDS
```

на:

```python
# Перегенерация не создаёт страницу заново — бюджет считается только по
# реально выбранным частям, а не всегда «по максимуму»: текст — один
# последовательный вызов генерации тела (_REGEN_TEXT_SECONDS); картинки — N
# последовательных текстовых промптов иллюстраций (_RETRY_PER_IMAGE_SECONDS
# каждый) плюс одна параллельная пачка генерации самих картинок
# (_REGEN_IMAGE_BATCH_SECONDS); обложка — промпт обложки плюс сама картинка
# (_REGEN_COVER_SECONDS). _REGEN_OVERHEAD_SECONDS — общий запас на загрузку
# файлов и update_page_text, один раз независимо от набора частей.
_REGEN_OVERHEAD_SECONDS = 300
_REGEN_IMAGE_BATCH_SECONDS = 365
_REGEN_TEXT_SECONDS = 366          # один вызов генерации тела статьи
_REGEN_COVER_SECONDS = 366 + 365   # промпт обложки + сама картинка


def _regen_time_limits(*, text: bool, image_count: int, cover: bool) -> tuple[int, int]:
    soft = _REGEN_OVERHEAD_SECONDS
    if text:
        soft += _REGEN_TEXT_SECONDS
    if image_count:
        soft += _RETRY_PER_IMAGE_SECONDS * image_count + _REGEN_IMAGE_BATCH_SECONDS
    if cover:
        soft += _REGEN_COVER_SECONDS
    return soft, soft + TIME_LIMIT_GAP_SECONDS
```

Заменить эндпоинт (было `regenerate_images` на `/regenerate-images`):

```python
@router.post("/articles/{article_id}/regenerate-images")
def regenerate_images(article_id: int, db: Session = Depends(get_db),
                      _user: User = Depends(get_current_user)):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(404, "статья не найдена")
    if article.status != "published":
        raise HTTPException(
            400, "перегенерация картинок доступна только для опубликованных статей")
    # Тот же приём анти-гонки, что у run()/retry() выше: перевод в
    # "выполняется" синхронно, до apply_async, — второй быстрый клик
    # увидит уже True и не поставит вторую задачу в очередь.
    if article.regenerating:
        raise HTTPException(400, "перегенерация картинок уже выполняется")
    article.regenerating = True
    db.commit()

    image_count = db.scalar(
        select(func.count(func.distinct(ArticleImage.position)))
        .where(ArticleImage.article_id == article.id, ArticleImage.kind == "content")
    ) or 0
    soft, hard = _regen_time_limits(image_count)
    regenerate_article_images.apply_async(args=[article.id], soft_time_limit=soft,
                                          time_limit=hard)
    return {"ok": True}
```

на:

```python
class RegenerateIn(BaseModel):
    text: bool = False
    images: bool = False
    cover: bool = False


@router.post("/articles/{article_id}/regenerate")
def regenerate(article_id: int, payload: RegenerateIn, db: Session = Depends(get_db),
               _user: User = Depends(get_current_user)):
    if not (payload.text or payload.images or payload.cover):
        raise HTTPException(400, "нужно выбрать хотя бы одну часть для перегенерации")
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(404, "статья не найдена")
    if article.status != "published":
        raise HTTPException(400, "перегенерация доступна только для опубликованных статей")
    # Тот же приём анти-гонки, что у run()/retry() выше: перевод в
    # "выполняется" синхронно, до apply_async, — второй быстрый клик
    # увидит уже True и не поставит вторую задачу в очередь.
    if article.regenerating:
        raise HTTPException(400, "перегенерация уже выполняется")
    article.regenerating = True
    db.commit()

    image_count = 0
    if payload.images:
        image_count = db.scalar(
            select(func.count(func.distinct(ArticleImage.position)))
            .where(ArticleImage.article_id == article.id, ArticleImage.kind == "content")
        ) or 0
    soft, hard = _regen_time_limits(text=payload.text, image_count=image_count,
                                    cover=payload.cover)
    regenerate_article.apply_async(
        args=[article.id],
        kwargs={"text": payload.text, "images": payload.images, "cover": payload.cover},
        soft_time_limit=soft, time_limit=hard)
    return {"ok": True}
```

Обновить `ArticleOut` — поле уже переименовано Task 1 в `regenerating: bool`, изменений не требуется.

- [ ] **Step 4: Запустить тесты**

Run: `docker compose run --rm --no-deps backend pytest tests/test_api_batches.py -v`
Expected: все тесты файла PASS.

- [ ] **Step 5: Прогнать весь бэкендный набор тестов**

Run: `docker compose run --rm --no-deps backend pytest -q`
Expected: все тесты проходят.

- [ ] **Step 6: Commit**

```bash
git add execution/backend/app/api/article_batches.py execution/backend/tests/test_api_batches.py
git commit -m "feat: эндпоинт POST /api/articles/{id}/regenerate (text/images/cover)"
```

---

### Task 8: Frontend — API-клиент + три кнопки на `BatchPage.tsx`

`BatchPage.tsx` — единственный потребитель `regenerateArticleImages`. Если
переименовать её в `api.ts` отдельным коммитом раньше, чем обновить
`BatchPage.tsx`, между двумя коммитами фронтенд не будет собираться
(`tsc` не найдёт `regenerateArticleImages`) — поэтому оба файла меняются
здесь одной задачей с одной итоговой сборкой и одним коммитом, тем же
принципом, что и в Task 6 для бэкенда.

**Files:**
- Modify: `execution/frontend/src/api.ts`
- Modify: `execution/frontend/src/pages/BatchPage.tsx`

#### Часть A: API-клиент

- [ ] **Step 1: Заменить функцию и добавить тип**

Поле `ArticleRow.regenerating` уже переименовано в Task 1 (замена строки `images_regenerating` → `regenerating`). Функция `regenerateArticleImages` эту замену не затронула — `sed` в Task 1 искал точную подстроку `images_regenerating`, а `regenerateArticleImages` (без нижнего подчёркивания) под неё не подходит. Поэтому в `execution/frontend/src/api.ts` она всё ещё называется по-старому и шлёт на `/regenerate-images` — меняем вручную.

Заменить:
```typescript
export const regenerateArticleImages = (id: number) =>
  api.post(`/articles/${id}/regenerate-images`)
```
на:
```typescript
export interface RegenerateParts { text?: boolean; images?: boolean; cover?: boolean }
export const regenerateArticle = (id: number, parts: RegenerateParts) =>
  api.post(`/articles/${id}/regenerate`, parts)
```

#### Часть B: три кнопки на `BatchPage.tsx`

- [ ] **Step 2: Импорт**

Заменить:
```typescript
import {
  ArticleRow, Batch, getBatch, regenerateArticleImages, retryArticle, runBatch, saveTopics,
} from '../api'
```
на:
```typescript
import {
  ArticleRow, Batch, getBatch, regenerateArticle, retryArticle, runBatch, saveTopics,
} from '../api'
```

(поллинг-условие `a.regenerating` в `useEffect` уже переименовано Task 1 — изменений не требуется.)

- [ ] **Step 3: Заменить колонку таблицы**

Заменить блок (строки с `title: '', width: 220` до конца объекта колонки):

```typescript
              {
                title: '', width: 220,
                render: (_, r: ArticleRow) => {
                  if (r.status === 'failed') {
                    return (
                      <Popconfirm title="Повторить генерацию этой статьи?"
                                  onConfirm={async () => { await retryArticle(r.id); load() }}>
                        <Button type="text" icon={<ReloadOutlined />} />
                      </Popconfirm>
                    )
                  }
                  if (r.status === 'published') {
                    return (
                      <Popconfirm title="Перегенерировать картинки в тексте статьи?"
                                  onConfirm={async () => {
                                    await regenerateArticleImages(r.id)
                                    load()
                                  }}>
                        <Button size="small" icon={<ReloadOutlined />}
                                loading={r.regenerating}
                                disabled={r.regenerating}>
                          Перегенерировать картинки
                        </Button>
                      </Popconfirm>
                    )
                  }
                  return null
                },
              },
```

на:

```typescript
              {
                title: '', width: 300,
                render: (_, r: ArticleRow) => {
                  if (r.status === 'failed') {
                    return (
                      <Popconfirm title="Повторить генерацию этой статьи?"
                                  onConfirm={async () => { await retryArticle(r.id); load() }}>
                        <Button type="text" icon={<ReloadOutlined />} />
                      </Popconfirm>
                    )
                  }
                  if (r.status === 'published') {
                    const regen = async (parts: { images?: boolean; cover?: boolean }) => {
                      await regenerateArticle(r.id, parts)
                      load()
                    }
                    return (
                      <Space direction="vertical" size={2}>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          Перегенерировать картинки
                        </Typography.Text>
                        <Space size={4}>
                          <Popconfirm title="Перегенерировать картинки внутри текста статьи?"
                                      onConfirm={() => regen({ images: true })}>
                            <Button size="small" loading={r.regenerating}
                                    disabled={r.regenerating}>
                              Только внутри
                            </Button>
                          </Popconfirm>
                          <Popconfirm title="Перегенерировать обложку статьи?"
                                      onConfirm={() => regen({ cover: true })}>
                            <Button size="small" loading={r.regenerating}
                                    disabled={r.regenerating}>
                              Обложку
                            </Button>
                          </Popconfirm>
                          <Popconfirm title="Перегенерировать все картинки статьи (внутри и обложку)?"
                                      onConfirm={() => regen({ images: true, cover: true })}>
                            <Button size="small" loading={r.regenerating}
                                    disabled={r.regenerating}>
                              Все
                            </Button>
                          </Popconfirm>
                        </Space>
                      </Space>
                    )
                  }
                  return null
                },
              },
```

- [ ] **Step 4: Проверить сборку**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/execution && docker compose run --rm frontend sh -c "npm install && npm run build"`
Expected: сборка без ошибок TypeScript/Vite — это первая точка, где `api.ts` и `BatchPage.tsx` проверяются вместе.

- [ ] **Step 5: Commit**

```bash
git add execution/frontend/src/api.ts execution/frontend/src/pages/BatchPage.tsx
git commit -m "feat: клиент + три кнопки перегенерации на странице партии"
```

---

### Task 9: Frontend — модалка «Перегенерировать по ID» на `ArticlesPage.tsx`

**Files:**
- Modify: `execution/frontend/src/pages/ArticlesPage.tsx`

- [ ] **Step 1: Импорты**

Заменить:
```typescript
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Button, Card, Form, InputNumber, Modal, Select, Space, Table, Tag, Typography,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { Batch, SiteBrief, createBatch, getBatches, getSites } from '../api'
```
на:
```typescript
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Button, Card, Form, InputNumber, Modal, Select, Space, Table, Tag, Typography, message,
} from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  Batch, RegenerateParts, SiteBrief, createBatch, getBatches, getSites, regenerateArticle,
} from '../api'
```

- [ ] **Step 2: Добавить конфигурацию кнопок и состояние модалки**

После объявления `STATUS` и `pluralizeImages` (перед `export default function ArticlesPage()`), добавить:

```typescript
const REGEN_OPTIONS: { label: string; parts: RegenerateParts }[] = [
  { label: 'Только текст', parts: { text: true } },
  { label: 'Только картинки внутри', parts: { images: true } },
  { label: 'Только обложку', parts: { cover: true } },
  { label: 'Все картинки и обложку', parts: { images: true, cover: true } },
  { label: 'Все', parts: { text: true, images: true, cover: true } },
]
```

Внутри `ArticlesPage`, после `const [open, setOpen] = useState(false)`, добавить:

```typescript
  const [regenOpen, setRegenOpen] = useState(false)
  const [regenId, setRegenId] = useState<number | null>(null)
```

- [ ] **Step 3: Добавить кнопку рядом с «Новая партия» и модалку**

Заменить:
```tsx
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Партии статей</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          Новая партия
        </Button>
      </Space>
```
на:
```tsx
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Партии статей</Typography.Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => setRegenOpen(true)}>
            Перегенерировать по ID
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            Новая партия
          </Button>
        </Space>
      </Space>
```

Добавить новую модалку сразу после существующей `<Modal open={open} ...>...</Modal>` (перед закрывающим `</>` компонента):

```tsx
      <Modal open={regenOpen} onCancel={() => setRegenOpen(false)} footer={null}
             title="Перегенерировать статью по ID">
        <Space direction="vertical" style={{ width: '100%' }} size={16}>
          <InputNumber min={1} style={{ width: '100%' }} placeholder="ID статьи"
                      value={regenId} onChange={setRegenId} />
          <Space wrap>
            {REGEN_OPTIONS.map(({ label, parts }) => (
              <Button key={label} disabled={!regenId}
                      onClick={async () => {
                        if (!regenId) return
                        try {
                          await regenerateArticle(regenId, parts)
                          message.success(`Запущено для статьи №${regenId}`)
                        } catch {
                          // Ошибка уже показана перехватчиком api.ts.
                        }
                      }}>
                {label}
              </Button>
            ))}
          </Space>
        </Space>
      </Modal>
```

ID-поле намеренно не сбрасывается после успешного запуска и модалка не закрывается сама — оператору может понадобиться прогнать несколько ID подряд.

- [ ] **Step 4: Проверить сборку**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/execution && docker compose run --rm frontend sh -c "npm install && npm run build"`
Expected: сборка без ошибок TypeScript/Vite.

- [ ] **Step 5: Ручная проверка в браузере**

Run: `docker compose up -d postgres redis && docker compose run --rm backend alembic upgrade head && docker compose up api worker frontend`

Открыть `http://localhost:3000`, зайти под менеджером:
- «Статьи» → «Перегенерировать по ID»: модалка открывается, поле требует ID, кнопки задизейблены без ID, после ввода ID активны; клик по любой кнопке уходит POST-ом (видно в Network) и не закрывает модалку.
- Партия с хотя бы одной статьёй `status="published"`: под статьёй — заголовок «Перегенерировать картинки» и три кнопки «Только внутри» / «Обложку» / «Все»; во время выполнения кнопки задизейблены и партия сама обновляется по опросу.

Реальная генерация требует настроенного ключа RouterAI — если его нет в этом окружении, ошибка конфигурации отобразится в развёрнутой строке статьи (`error_text`), что тоже подтверждает корректность связки UI → API → задача.

- [ ] **Step 6: Commit**

```bash
git add execution/frontend/src/pages/ArticlesPage.tsx
git commit -m "feat: модалка «Перегенерировать по ID» на странице «Статьи»"
```

---

## Итоговая проверка

- [ ] `docker compose run --rm --no-deps backend pytest -q` — весь бэкендный набор тестов зелёный.
- [ ] `docker compose run --rm backend alembic upgrade head` на чистой Postgres из Task 1 отрабатывает без ошибок (и downgrade -1 / upgrade head тоже).
- [ ] `docker compose run --rm frontend sh -c "npm install && npm run build"` — фронтенд собирается без ошибок TypeScript.
- [ ] Ручная проверка UI из Task 9 Step 5 пройдена.
- [ ] `grep -rn "images_regenerating\|regenerate_images_for\|regenerate_article_images\b\|regenerateArticleImages" execution/backend/app execution/backend/tests execution/frontend/src` — пусто (исторический файл `execution/backend/alembic/versions/bcba3fe8e22e_article_image_regeneration.py` вне области этой проверки — он и не должен был меняться, см. Task 1).
