# Перегенерация картинок в опубликованной статье — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** На странице партии статей у каждой опубликованной статьи появляется кнопка «Перегенерировать картинки», которая заново генерирует картинки, встроенные в текст статьи, заливает их под новым (версионированным) именем файла, обновляет пути в тексте и пушит изменённый текст на уже опубликованную страницу сайта — не трогая обложку и не удаляя старые картинки.

**Architecture:** Асинхронная Celery-задача `regenerate_article_images`, по образцу уже существующей `retry_article`. Основная логика — новый метод `ArticleBuilder.regenerate_content_images()` (`app/articles/builder.py`), переиспользующий существующие приватные хелперы билдера (`_image_prompt`, `_render_content_image`, `_record_usage`). Версионирование имён файлов — необязательный параметр `version` у уже существующей функции `image_filename()`. Флаг `Article.images_regenerating` защищает от повторного клика и служит сигналом для поллинга на фронте.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, Celery, pytest (те же, что и во всём бэкенде); React + antd на фронте.

**Спека:** `directions/2026-08-09-article-image-regeneration-design.md`

---

## Структура файлов

```
execution/backend/
  alembic/versions/<new>_article_image_regeneration.py   Create: version + images_regenerating
  app/models/article.py                                  Modify: +Article.images_regenerating, +ArticleImage.version
  app/articles/builder.py                                Modify: image_filename(version=), ArticleBuilder.regenerate_content_images(), regenerate_images_for()
  app/tasks.py                                            Modify: regenerate_article_images_sync + Celery task
  app/api/article_batches.py                              Modify: POST /articles/{id}/regenerate-images, ArticleOut.images_regenerating, _regen_time_limits
  tests/test_articles_builder.py                           Modify: FakeSiteClient.update_page_text + новые тесты
  tests/test_tasks.py                                      Modify: новые тесты regenerate_article_images_sync
  tests/test_api_batches.py                                Modify: no_celery + новые тесты эндпоинта

execution/frontend/
  src/api.ts                    Modify: ArticleRow.images_regenerating, regenerateArticleImages()
  src/pages/BatchPage.tsx        Modify: кнопка «Перегенерировать картинки», условие поллинга
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

### Task 1: Миграция — `article_images.version` и `articles.images_regenerating`

**Files:**
- Create: `execution/backend/alembic/versions/<generated>_article_image_regeneration.py`

- [ ] **Step 1: Сгенерировать пустую ревизию**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/execution && docker compose run --rm backend alembic revision -m "article image regeneration"`

Expected: создан файл `execution/backend/alembic/versions/<hash>_article_image_regeneration.py` с автосгенерированными `revision` и `down_revision = '9864d416847d'` (текущая голова, см. `9864d416847d_widen_company_candidate_phone.py`).

- [ ] **Step 2: Заполнить upgrade/downgrade**

Открыть сгенерированный файл и заменить тело на:

```python
"""article image regeneration

Кнопка «Перегенерировать картинки» на странице партии (см.
directions/2026-08-09-article-image-regeneration-design.md) заливает новый
раунд content-картинок под versioned-именем файла, не удаляя старые.
`article_images.version` различает раунды одной и той же позиции (1 — то,
что сгенерировано при первой публикации статьи, 2+ — последующие раунды
перегенерации). `articles.images_regenerating` защищает от повторного клика,
пока фоновая Celery-задача не закончила, и служит фронту сигналом для
поллинга статуса партии.

Revision ID: <как сгенерировал alembic>
Revises: 9864d416847d
Create Date: <как сгенерировал alembic>

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '<как сгенерировал alembic>'
down_revision: Union[str, None] = '9864d416847d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('article_images',
                  sa.Column('version', sa.Integer(), nullable=False,
                            server_default='1'))
    op.add_column('articles',
                  sa.Column('images_regenerating', sa.Boolean(), nullable=False,
                            server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('articles', 'images_regenerating')
    op.drop_column('article_images', 'version')
```

Не менять `revision`/`Revision ID`/`Create Date`, которые сгенерировал alembic — только `down_revision` должен остаться `'9864d416847d'` (alembic обычно проставляет его сам, так как это единственная голова).

- [ ] **Step 3: Применить миграцию на реальном Postgres и проверить откат**

Run:
```bash
docker compose up -d postgres
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend alembic downgrade -1
docker compose run --rm backend alembic upgrade head
```
Expected: все три команды завершаются без ошибок (проверка, что и upgrade, и downgrade синтаксически и семантически корректны на реальной БД, а не только в SQLite-тестах, которые эту миграцию не исполняют).

- [ ] **Step 4: Commit**

```bash
git add execution/backend/alembic/versions/*_article_image_regeneration.py
git commit -m "feat: миграция version/images_regenerating для перегенерации картинок статьи"
```

---

### Task 2: Модели — `Article.images_regenerating`, `ArticleImage.version`

**Files:**
- Modify: `execution/backend/app/models/article.py`

- [ ] **Step 1: Добавить колонки в модели**

В классе `Article` (после `error_text`, перед `remote_page_id`) добавить:

```python
    images_regenerating: Mapped[bool] = mapped_column(default=False)
```

В классе `ArticleImage` (после `cost`) добавить:

```python
    version: Mapped[int] = mapped_column(Integer, default=1)
```

- [ ] **Step 2: Написать тест дефолтных значений**

Файл `execution/backend/tests/test_models_article.py` — открыть и посмотреть существующий стиль тестов, затем добавить:

```python
def test_new_article_and_image_default_regeneration_fields(db_session, admin):
    from app.models.article import Article, ArticleBatch, ArticleImage

    batch = ArticleBatch(requested_count=1, created_by_id=admin.id)
    db_session.add(batch)
    db_session.commit()
    article = Article(batch_id=batch.id, topic="Тема")
    db_session.add(article)
    db_session.commit()
    image = ArticleImage(article_id=article.id, kind="content", position=1)
    db_session.add(image)
    db_session.commit()

    assert article.images_regenerating is False
    assert image.version == 1
```

- [ ] **Step 3: Запустить тест**

Run: `docker compose run --rm --no-deps backend pytest tests/test_models_article.py -v`
Expected: PASS (SQLAlchemy Python-side `default=` применяется сразу при создании объекта, миграция для SQLite-тестов не нужна — `Base.metadata.create_all` в `conftest.py` берёт схему прямо из моделей).

- [ ] **Step 4: Commit**

```bash
git add execution/backend/app/models/article.py execution/backend/tests/test_models_article.py
git commit -m "feat: поля images_regenerating и version в моделях статьи"
```

---

### Task 3: `image_filename()` — версионирование имени файла

**Files:**
- Modify: `execution/backend/app/articles/builder.py:41-56`
- Test: `execution/backend/tests/test_articles_builder.py`

- [ ] **Step 1: Написать падающий тест**

В `execution/backend/tests/test_articles_builder.py`, рядом с `test_image_filename_is_deterministic` (строка 110), добавить:

```python
def test_image_filename_adds_version_suffix_from_second_round():
    assert image_filename(7, 2, version=1) == "cp-article-7-2.webp"
    assert image_filename(7, 2, version=2) == "cp-article-7-2_v2.webp"
    assert image_filename(7, 0, version=3) == "cp-article-7-cover_v3.webp"
```

- [ ] **Step 2: Проверить, что тест падает**

Run: `docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py::test_image_filename_adds_version_suffix_from_second_round -v`
Expected: FAIL с `TypeError: image_filename() got an unexpected keyword argument 'version'`.

- [ ] **Step 3: Реализовать**

В `execution/backend/app/articles/builder.py` заменить функцию `image_filename` (строки 41-56):

```python
def image_filename(article_id: int, position: int, version: int = 1) -> str:
    """position=0 — обложка, дальше контентные по порядку. version=1 — то,
    что сгенерировано при первой публикации статьи (имя не меняется, чтобы
    не сломать обратную совместимость со старыми файлами/тестами); version>1
    — раунд перегенерации картинок уже опубликованной статьи (кнопка
    «Перегенерировать картинки», directions/2026-08-09-...-design.md) —
    добавляет суффикс _vN, чтобы не перезаписать предыдущий файл в
    filemanager сайта (см. остальной докстринг ниже — та же коллизия имён,
    которую он уже однажды вызвал).

    Префикс "cp-article-" (а не просто "article_") — намеренно: на
    stroybaza-samara.ru в той же папке filemanager (ARTICLE_IMG_DIR) уже
    лежат файлы article_1-*.webp..article_6-*.webp, залитые старым CLI-
    пайплайном (execution/articles/, батч по номеру темы в манифесте, а не
    по id статьи). Т.к. filemanager при совпадении имени молча
    перезаписывает файл без суффикса, а id статей здесь — сквозной
    автоинкремент по всей таблице Article, три первые статьи нового сервиса
    на этом сайте получили id 4/5/6 и своими картинками затёрли/были
    затёрты картинками старых статей article_4/5/6 (пиломатериалы,
    пароизоляция, герметик) — баг был обнаружен на проде. Новый префикс не
    пересекается со старой схемой ни при каком id."""
    suffix = "cover" if position == 0 else str(position)
    version_suffix = "" if version <= 1 else f"_v{version}"
    return f"cp-article-{article_id}-{suffix}{version_suffix}.webp"
```

- [ ] **Step 4: Проверить, что тесты проходят**

Run: `docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -v -k image_filename`
Expected: оба теста (`test_image_filename_is_deterministic`, `test_image_filename_adds_version_suffix_from_second_round`) PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/articles/builder.py execution/backend/tests/test_articles_builder.py
git commit -m "feat: версионированное имя файла картинки статьи"
```

---

### Task 4: `ArticleBuilder.regenerate_content_images()`

**Files:**
- Modify: `execution/backend/app/articles/builder.py`
- Test: `execution/backend/tests/test_articles_builder.py`

- [ ] **Step 1: Добавить `update_page_text` в `FakeSiteClient` теста**

В `execution/backend/tests/test_articles_builder.py`, в классе `FakeSiteClient` (строки 34-58), добавить поле и метод:

```python
class FakeSiteClient:
    def __init__(self):
        self.uploaded = []
        self.created = None
        self.cover = None
        self.fetched_pages = []
        self.updated_text = None
```

и после `set_page_cover`:

```python
    def update_page_text(self, page_id, html):
        self.updated_text = (page_id, html)
        return {"id": page_id}
```

- [ ] **Step 2: Написать падающие тесты**

В том же файле, в конце (после `test_llm_usage_model_matches_kind_not_always_text_client`), добавить:

```python
# --- Перегенерация картинок уже опубликованной статьи ---


def test_regenerate_content_images_uploads_versioned_files_and_updates_body(
        db_session, prepared):
    from app.models.article import ArticleImage
    from app.sites.client import ARTICLE_IMG_DIR

    site_client = FakeSiteClient()
    paths = image_paths_for(prepared.article.id, 2)
    body = {
        "title": "Чем утеплить каркасный дом",
        "html": f"<article class='post'><p>Текст</p>"
                f"<img src='{paths[0]}'><img src='{paths[1]}'></article>",
        "meta_description": "описание", "meta_keywords": "утепление",
    }
    builder = make_builder(db_session, prepared, site_client, body=body)
    builder.build()
    assert paths[0] in prepared.article.body_html
    assert paths[1] in prepared.article.body_html

    prepared.article.images_regenerating = True
    db_session.commit()
    builder.regenerate_content_images()

    new_path_1 = f"/media/{ARTICLE_IMG_DIR}{image_filename(prepared.article.id, 1, version=2)}"
    new_path_2 = f"/media/{ARTICLE_IMG_DIR}{image_filename(prepared.article.id, 2, version=2)}"
    assert new_path_1 in prepared.article.body_html
    assert new_path_2 in prepared.article.body_html
    assert paths[0] not in prepared.article.body_html
    assert paths[1] not in prepared.article.body_html

    assert site_client.uploaded == [
        "cp-article-1-1.webp", "cp-article-1-2.webp",
        "cp-article-1-1_v2.webp", "cp-article-1-2_v2.webp",
    ]
    assert site_client.updated_text == (501, prepared.article.body_html)
    assert prepared.article.status == "published"
    assert prepared.article.images_regenerating is False
    assert prepared.article.error_text == ""

    images = db_session.query(ArticleImage).filter_by(
        article_id=prepared.article.id, kind="content").order_by(
        ArticleImage.position, ArticleImage.version).all()
    assert [(i.position, i.version, i.remote_path) for i in images] == [
        (1, 1, paths[0]), (1, 2, new_path_1),
        (2, 1, paths[1]), (2, 2, new_path_2),
    ]


def test_regenerate_content_images_partial_failure_keeps_old_path_for_failed_position(
        db_session, prepared):
    site_client = FakeSiteClient()
    paths = image_paths_for(prepared.article.id, 2)
    body = {
        "title": "Чем утеплить каркасный дом",
        "html": f"<p>{paths[0]}</p><p>{paths[1]}</p>",
        "meta_description": "", "meta_keywords": "",
    }
    builder = ArticleBuilder(
        db=db_session, article=prepared.article, site=prepared.site,
        text_client=SequencedTextClient(body), image_generator=FakeImageGenerator(),
        site_client=site_client,
        image_params={"size": "1536x1024", "quality": "medium", "workers": 2},
        watermark_bytes=b"", job_run_id=None,
    )
    builder.build()

    builder.image_generator = PartialFailureImageGenerator(fail_on="2")
    builder.text_client = SequencedTextClient(body)
    builder.regenerate_content_images()

    assert paths[0] not in prepared.article.body_html   # позиция 1 успела обновиться
    assert paths[1] in prepared.article.body_html        # позиция 2 осталась старой
    assert "перегенерировано 1/2" in prepared.article.error_text
    assert prepared.article.images_regenerating is False
    assert prepared.article.status == "published"


def test_regenerate_content_images_without_existing_images_records_error(db_session, prepared):
    prepared.article.status = "published"
    prepared.article.body_html = "<p>текст без картинок</p>"
    prepared.article.remote_page_id = 501
    prepared.article.images_regenerating = True
    db_session.commit()

    builder = make_builder(db_session, prepared)
    builder.regenerate_content_images()

    assert "нет картинок" in prepared.article.error_text
    assert prepared.article.images_regenerating is False
    assert builder.image_generator.calls == []


def test_regenerate_content_images_second_round_uses_next_version(db_session, prepared):
    site_client = FakeSiteClient()
    paths = image_paths_for(prepared.article.id, 2)
    body = {
        "title": "Чем утеплить каркасный дом",
        "html": f"<p>{paths[0]}</p><p>{paths[1]}</p>",
        "meta_description": "", "meta_keywords": "",
    }
    builder = make_builder(db_session, prepared, site_client, body=body)
    builder.build()
    builder.regenerate_content_images()
    builder.regenerate_content_images()

    from app.models.article import ArticleImage

    versions = sorted({i.version for i in db_session.query(ArticleImage).filter_by(
        article_id=prepared.article.id, kind="content").all()})
    assert versions == [1, 2, 3]
    assert image_filename(prepared.article.id, 1, version=3) in prepared.article.body_html
```

- [ ] **Step 3: Проверить, что тесты падают**

Run: `docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -k regenerate_content_images -v`
Expected: FAIL с `AttributeError: 'ArticleBuilder' object has no attribute 'regenerate_content_images'`.

- [ ] **Step 4: Реализовать метод**

В `execution/backend/app/articles/builder.py`, добавить публичный метод в класс `ArticleBuilder`, сразу после `build()` (после строки 108, перед `# --- шаги ---`):

```python
    def regenerate_content_images(self) -> None:
        """Перегенерирует все content-картинки уже опубликованной статьи
        новым раундом версий, заменяет их пути в body_html и пушит
        обновлённый текст на уже созданную страницу сайта. Обложка
        (kind="cover") не трогается — она не часть текста статьи, а
        отдельный механизм страницы (teaser_image, см. _attach_cover).
        Старые ArticleImage и файлы на сайте не удаляются: новый раунд
        просто получает следующий version и новое имя файла
        (image_filename с version>1, см. её докстринг)."""
        content_images = [i for i in self.article.images if i.kind == "content"]
        positions = sorted({i.position for i in content_images})
        if not positions:
            self.article.error_text = "нет картинок для перегенерации"
            self.article.images_regenerating = False
            self.db.commit()
            return

        next_version = max(i.version for i in content_images) + 1
        # На случай, если предыдущий раунд был частичным (не все позиции
        # успели перегенерироваться) — версии разных позиций могут
        # разойтись, поэтому "старый путь" ищем на позицию, а не на
        # next_version - 1 глобально.
        old_path_by_position = {
            position: max((i for i in content_images if i.position == position),
                          key=lambda i: i.version).remote_path
            for position in positions
        }
        prompts = {
            position: self._image_prompt("content_image", {
                "topic": self.article.topic,
                "paragraph": f"иллюстрация {position} из {len(positions)}",
                "image_style": self.site.image_style_prompt,
            })
            for position in positions
        }

        updated = 0
        first_error: Exception | None = None
        # Та же логика, что в _generate_content_images: ждём ВСЕ futures,
        # не теряем уже оплаченные успешные результаты соседей из-за одной
        # упавшей генерации.
        with ThreadPoolExecutor(max_workers=self.image_params["workers"]) as pool:
            futures = [pool.submit(self._render_content_image, position, prompts[position])
                      for position in positions]
            for future in as_completed(futures):
                try:
                    position, prompt, data, cost = future.result()
                except Exception as exc:  # noqa: BLE001 — см. комментарий выше
                    if first_error is None:
                        first_error = exc
                    continue
                filename = image_filename(self.article.id, position, version=next_version)
                path = self.site_client.upload_file(data, filename, ARTICLE_IMG_DIR)
                self.db.add(ArticleImage(article_id=self.article.id, kind="content",
                                         position=position, version=next_version,
                                         prompt=prompt, remote_path=path, cost=cost))
                self._record_usage("image", 0, 0, cost)
                self.article.body_html = self.article.body_html.replace(
                    old_path_by_position[position], path)
                updated += 1
                self.db.commit()

        if updated:
            self.site_client.update_page_text(self.article.remote_page_id, self.article.body_html)

        if first_error is not None:
            self.article.error_text = (
                f"перегенерировано {updated}/{len(positions)} картинок, "
                f"ошибка: {first_error}")
        else:
            self.article.error_text = ""
        self.article.images_regenerating = False
        self.db.commit()
```

- [ ] **Step 5: Запустить тесты**

Run: `docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -v`
Expected: все тесты файла PASS, включая четыре новых.

- [ ] **Step 6: Commit**

```bash
git add execution/backend/app/articles/builder.py execution/backend/tests/test_articles_builder.py
git commit -m "feat: ArticleBuilder.regenerate_content_images — перегенерация картинок в тексте статьи"
```

---

### Task 5: `regenerate_images_for()` — сборка билдера из настроек БД

**Files:**
- Modify: `execution/backend/app/articles/builder.py` (конец файла, после `build_for`)

- [ ] **Step 1: Добавить функцию**

В конец `execution/backend/app/articles/builder.py`, после существующей функции `build_for`:

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

Эта функция не имеет собственного unit-теста (как и `build_for` — она чистая сборка зависимостей без ветвлений), логика самого раунда перегенерации уже покрыта тестами `regenerate_content_images` из Task 4, а обработка ошибок конфигурации (`AIConfigError`) будет покрыта на уровне `app/tasks.py` в Task 6.

- [ ] **Step 2: Проверить, что ничего не сломалось**

Run: `docker compose run --rm --no-deps backend pytest tests/test_articles_builder.py -v`
Expected: все тесты PASS (новая функция ничего не меняет в существующем коде).

- [ ] **Step 3: Commit**

```bash
git add execution/backend/app/articles/builder.py
git commit -m "feat: regenerate_images_for — сборка ArticleBuilder для перегенерации картинок"
```

---

### Task 6: `app/tasks.py` — Celery-задача `regenerate_article_images`

**Files:**
- Modify: `execution/backend/app/tasks.py`
- Test: `execution/backend/tests/test_tasks.py`

- [ ] **Step 1: Написать падающие тесты**

В `execution/backend/tests/test_tasks.py`, после `test_retry_article_without_site_marks_article_failed` (см. окрестности строки 321), добавить:

```python
# --- перегенерация картинок опубликованной статьи ---


def test_regenerate_article_images_sync_calls_builder_and_finishes_job(
        db_session, batch, site, monkeypatch):
    from app.tasks import regenerate_article_images_sync

    article = Article(batch_id=batch.id, site_id=site.id, topic="Тема",
                      status="published", remote_page_id=501,
                      images_regenerating=True)
    db_session.add(article)
    db_session.commit()

    calls = []
    monkeypatch.setattr(
        "app.tasks.regenerate_images_for",
        lambda db, a, s, sc, job_id: calls.append((a.id, s.id, job_id)))
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: SimpleNamespace())

    regenerate_article_images_sync(db_session, article.id)
    db_session.refresh(article)

    assert len(calls) == 1 and calls[0][0] == article.id and calls[0][1] == site.id
    assert article.images_regenerating is False


def test_regenerate_article_images_sync_skips_non_published_article(
        db_session, batch, site, monkeypatch):
    from app.tasks import regenerate_article_images_sync

    article = Article(batch_id=batch.id, site_id=site.id, topic="Тема",
                      status="draft", images_regenerating=True)
    db_session.add(article)
    db_session.commit()

    calls = []
    monkeypatch.setattr("app.tasks.regenerate_images_for", lambda *a, **k: calls.append(1))

    regenerate_article_images_sync(db_session, article.id)
    db_session.refresh(article)

    assert calls == []
    assert article.images_regenerating is False


def test_regenerate_article_images_sync_ai_config_error_marks_failed(
        db_session, batch, site, monkeypatch):
    from app.ai.factory import AIConfigError
    from app.tasks import regenerate_article_images_sync

    article = Article(batch_id=batch.id, site_id=site.id, topic="Тема",
                      status="published", remote_page_id=501,
                      images_regenerating=True)
    db_session.add(article)
    db_session.commit()

    def broken(db, article, site, site_client, job_run_id):
        raise AIConfigError("ключ RouterAI не задан — заполните routerai_api_key")

    monkeypatch.setattr("app.tasks.regenerate_images_for", broken)
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: SimpleNamespace())

    regenerate_article_images_sync(db_session, article.id)
    db_session.refresh(article)

    assert article.images_regenerating is False
    assert "ключ" in article.error_text
    assert article.status == "published"   # перегенерация не трогает статус статьи


def test_regenerate_article_images_sync_without_site_marks_failed(db_session, admin):
    from app.tasks import regenerate_article_images_sync

    orphan_batch = ArticleBatch(site_id=None, requested_count=1, created_by_id=admin.id)
    db_session.add(orphan_batch)
    db_session.commit()
    article = Article(batch_id=orphan_batch.id, site_id=None, topic="Тема",
                      status="published", remote_page_id=501,
                      images_regenerating=True)
    db_session.add(article)
    db_session.commit()

    regenerate_article_images_sync(db_session, article.id)
    db_session.refresh(article)

    assert article.images_regenerating is False
    assert "удал" in article.error_text
```

- [ ] **Step 2: Проверить, что тесты падают**

Run: `docker compose run --rm --no-deps backend pytest tests/test_tasks.py -k regenerate_article_images -v`
Expected: FAIL с `ImportError: cannot import name 'regenerate_article_images_sync' from 'app.tasks'`.

- [ ] **Step 3: Реализовать**

В `execution/backend/app/tasks.py`:

Изменить импорт (строка 13):
```python
from app.articles.builder import build_for, regenerate_images_for
```

Добавить после блока `retry_article` (после строки 308, `db.close()` внутри `def retry_article`, перед секцией `# --- строители: сборка партии ---`):

```python
# --- перегенерация картинок опубликованной статьи ---

def regenerate_article_images_sync(db, article_id: int) -> None:
    article = db.get(Article, article_id)
    if article.status != "published":
        # Гонка с эндпоинтом (app/api/article_batches.py, regenerate_images):
        # он уже отклоняет неопубликованные статьи синхронно, сюда можно
        # попасть только если статус успел измениться между постановкой
        # задачи и её реальным стартом. Тихий выход, тот же стиль, что и у
        # generate_topics_sync при повторной постановке той же задачи.
        article.images_regenerating = False
        db.commit()
        return

    site = db.get(Site, article.site_id) if article.site_id is not None else None
    if site is None:
        article.images_regenerating = False
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
        article.images_regenerating = False
        article.error_text = "превышен лимит времени задачи"
        db.commit()
        _finish_job(db, job, "failed", article.error_text)
        return
    except (AIConfigError, SecretDecryptionError) as exc:
        article.images_regenerating = False
        article.error_text = str(exc)
        db.commit()
        _finish_job(db, job, "failed", str(exc))
        return

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

- [ ] **Step 4: Запустить тесты**

Run: `docker compose run --rm --no-deps backend pytest tests/test_tasks.py -v`
Expected: все тесты файла PASS, включая четыре новых.

- [ ] **Step 5: Commit**

```bash
git add execution/backend/app/tasks.py execution/backend/tests/test_tasks.py
git commit -m "feat: Celery-задача regenerate_article_images"
```

---

### Task 7: API — `POST /api/articles/{id}/regenerate-images`

**Files:**
- Modify: `execution/backend/app/api/article_batches.py`
- Test: `execution/backend/tests/test_api_batches.py`

- [ ] **Step 1: Расширить фикстуру `no_celery` и написать падающие тесты**

В `execution/backend/tests/test_api_batches.py`, в фикстуре `no_celery` (строки 17-32), добавить после патча `retry_article.apply_async`:

```python
    monkeypatch.setattr(
        "app.api.article_batches.regenerate_article_images.apply_async",
        lambda args, **kwargs: sent.append(("regenerate", args[0], kwargs)) or
        type("R", (), {"id": "task-4"})())
```

В конец файла добавить:

```python
def test_regen_time_limits_grow_with_image_count():
    from app.api.article_batches import _regen_time_limits

    soft_one, hard_one = _regen_time_limits(1)
    soft_many, _ = _regen_time_limits(5)
    assert soft_many > soft_one
    assert hard_one > soft_one


def test_regenerate_images_unknown_article_404(manager_client, no_celery):
    resp = manager_client.post("/api/articles/999/regenerate-images")
    assert resp.status_code == 404


def test_regenerate_images_requires_published_article(manager_client, db_session,
                                                       site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема", status="draft")
    db_session.add(article)
    db_session.commit()

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate-images")
    assert resp.status_code == 400


def test_regenerate_images_starts_task_for_published_article(manager_client, db_session,
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

    resp = manager_client.post(f"/api/articles/{article.id}/regenerate-images")

    assert resp.status_code == 200
    assert any(entry[:2] == ("regenerate", article.id) for entry in no_celery)
    db_session.refresh(article)
    assert article.images_regenerating is True


def test_regenerate_images_twice_dispatches_once(manager_client, db_session, site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501)
    db_session.add(article)
    db_session.commit()

    first = manager_client.post(f"/api/articles/{article.id}/regenerate-images")
    second = manager_client.post(f"/api/articles/{article.id}/regenerate-images")

    assert first.status_code == 200
    assert second.status_code == 400
    dispatches = [entry for entry in no_celery if entry[0] == "regenerate"]
    assert len(dispatches) == 1


def test_batch_detail_includes_images_regenerating_flag(manager_client, db_session,
                                                         site_id, no_celery):
    from app.models.article import Article

    batch_id = manager_client.post("/api/article-batches",
                                   json={"site_id": site_id, "count": 1}).json()["id"]
    article = Article(batch_id=batch_id, site_id=site_id, topic="Тема",
                      status="published", remote_page_id=501, images_regenerating=True)
    db_session.add(article)
    db_session.commit()

    body = manager_client.get(f"/api/article-batches/{batch_id}").json()
    assert body["articles"][0]["images_regenerating"] is True
```

- [ ] **Step 2: Проверить, что тесты падают**

Run: `docker compose run --rm --no-deps backend pytest tests/test_api_batches.py -k "regen or regenerate" -v`
Expected: FAIL — `no_celery` фикстура падает с `AttributeError` (нет `regenerate_article_images` в `app.api.article_batches`), либо 404/`KeyError` на отсутствующее поле `images_regenerating`.

- [ ] **Step 3: Реализовать**

В `execution/backend/app/api/article_batches.py`:

Изменить импорты (строки 5, 9, 12):
```python
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.article import Article, ArticleBatch, ArticleImage
from app.models.site import Site
from app.models.user import User
from app.tasks import generate_topics, regenerate_article_images, retry_article, run_batch
```

Добавить поле в `ArticleOut` (после `error_text`, строка 30):
```python
class ArticleOut(BaseModel):
    id: int
    topic: str
    title: str
    status: str
    remote_url: str
    error_text: str
    images_regenerating: bool
```

Обновить `_to_out` (строки 53-55):
```python
        articles=[ArticleOut(id=a.id, topic=a.topic, title=a.title, status=a.status,
                             remote_url=a.remote_url, error_text=a.error_text,
                             images_regenerating=a.images_regenerating)
                  for a in batch.articles],
```

Добавить константы и хелпер лимитов времени после `_retry_time_limits` (после строки 177):
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

Добавить эндпоинт в конец файла, после `retry`:
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
    if article.images_regenerating:
        raise HTTPException(400, "перегенерация картинок уже выполняется")
    article.images_regenerating = True
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

- [ ] **Step 4: Запустить тесты**

Run: `docker compose run --rm --no-deps backend pytest tests/test_api_batches.py -v`
Expected: все тесты файла PASS.

- [ ] **Step 5: Прогнать весь бэкендный набор тестов**

Run: `docker compose run --rm --no-deps backend pytest -q`
Expected: все тесты проходят (регрессия по всему бэкенду).

- [ ] **Step 6: Commit**

```bash
git add execution/backend/app/api/article_batches.py execution/backend/tests/test_api_batches.py
git commit -m "feat: эндпоинт POST /api/articles/{id}/regenerate-images"
```

---

### Task 8: Frontend — API-клиент

**Files:**
- Modify: `execution/frontend/src/api.ts`

- [ ] **Step 1: Добавить поле в тип и новую функцию**

В `execution/frontend/src/api.ts` изменить `ArticleRow` (строки 68-71):
```typescript
export interface ArticleRow {
  id: number; topic: string; title: string; status: string
  remote_url: string; error_text: string; images_regenerating: boolean
}
```

После `export const retryArticle = ...` (строка 146) добавить:
```typescript
export const regenerateArticleImages = (id: number) =>
  api.post(`/articles/${id}/regenerate-images`)
```

- [ ] **Step 2: Проверить типизацию**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/execution && docker compose run --rm frontend sh -c "npm install && npx tsc --noEmit"`
Expected: без ошибок (новое поле пока нигде не используется — TS не требует, чтобы весь код был обновлён немедленно, раз `ArticleRow` создаётся только из ответа API, а не литералом в коде).

- [ ] **Step 3: Commit**

```bash
git add execution/frontend/src/api.ts
git commit -m "feat: клиент для POST /articles/{id}/regenerate-images"
```

---

### Task 9: Frontend — кнопка на `BatchPage.tsx`

**Files:**
- Modify: `execution/frontend/src/pages/BatchPage.tsx`

- [ ] **Step 1: Импортировать новую функцию**

Строка 7 — изменить:
```typescript
import { ArticleRow, Batch, getBatch, regenerateArticleImages, retryArticle, runBatch, saveTopics } from '../api'
```

- [ ] **Step 2: Расширить условие автообновления**

Строки 54-61 — заменить:
```typescript
  useEffect(() => {
    if (!batch) return
    const active = batch.status === 'topics_pending' || batch.status === 'running'
      || batch.articles.some(a => a.status === 'generating' || a.images_regenerating)
    if (!active) return
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  }, [batch])
```

- [ ] **Step 3: Добавить кнопку в таблицу статей**

Строки 206-214 (колонка с иконкой retry) — заменить:
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
                                loading={r.images_regenerating}
                                disabled={r.images_regenerating}>
                          Перегенерировать картинки
                        </Button>
                      </Popconfirm>
                    )
                  }
                  return null
                },
              },
```

- [ ] **Step 4: Проверить сборку**

Run: `cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/execution && docker compose run --rm frontend sh -c "npm install && npm run build"`
Expected: сборка завершается без ошибок TypeScript/Vite.

- [ ] **Step 5: Ручная проверка в браузере**

Run: `docker compose up -d postgres redis && docker compose run --rm backend alembic upgrade head && docker compose up api worker frontend`

Открыть `http://localhost:3000`, зайти под менеджером, открыть партию с хотя бы одной статьёй `status="published"` (можно проставить статус вручную через `docker compose exec postgres psql -U app -d content` на тестовой записи, если под рукой нет реально опубликованной статьи). Проверить:
- кнопка «Перегенерировать картинки» видна только у статей со статусом «Черновик на сайте» (published);
- клик открывает Popconfirm, после подтверждения кнопка не даёт кликнуть повторно, пока идёт перегенерация;
- после завершения (или ошибки RouterAI, если ключ не настроен в этом окружении — тогда в развёрнутой строке появится текст ошибки) таблица сама обновляется без ручного refresh.

Так как реальная генерация требует настроенного ключа RouterAI и живого сайта, конечный happy-path (новые картинки на сайте) можно не проверять в этом окружении — важно убедиться, что UI ведёт себя корректно (запрос уходит, кнопка блокируется, партия поллится, ошибка конфигурации отображается).

- [ ] **Step 6: Commit**

```bash
git add execution/frontend/src/pages/BatchPage.tsx
git commit -m "feat: кнопка «Перегенерировать картинки» на странице партии"
```

---

## Итоговая проверка

- [ ] `docker compose run --rm --no-deps backend pytest -q` — весь бэкендный набор тестов зелёный.
- [ ] `docker compose run --rm backend alembic upgrade head` на чистой Postgres из Task 1 отрабатывает без ошибок.
- [ ] `docker compose run --rm frontend sh -c "npm install && npm run build"` — фронтенд собирается без ошибок TypeScript.
- [ ] Ручная проверка UI из Task 9 Step 5 пройдена.
