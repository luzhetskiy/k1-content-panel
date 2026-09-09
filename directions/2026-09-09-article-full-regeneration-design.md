# Перегенерация текста/картинок/обложки статьи — дизайн

Дата: 2026-09-09
Статус: на утверждение
Базовые документы: `directions/2026-08-09-article-image-regeneration-design.md`
(предыдущая, более узкая версия этой же фичи — только контентные картинки),
`execution/backend/app/articles/builder.py`, `execution/backend/app/tasks.py`,
`execution/backend/app/api/article_batches.py`, `execution/backend/app/sites/client.py`,
`execution/frontend/src/pages/BatchPage.tsx`, `execution/frontend/src/pages/ArticlesPage.tsx`

Существующая кнопка «Перегенерировать картинки» на странице партии умеет
перегенерировать только контентные картинки внутри текста статьи. Этот
документ расширяет механизм до трёх независимых, компонуемых частей —
**текст**, **картинки внутри текста**, **обложка** — и добавляет два
интерфейса запуска: компактные кнопки на странице партии и инструмент
«по ID статьи» на странице «Статьи».

## 1. Почему нельзя просто добавить два новых метода независимо

Картинки внутри статьи встроены в `body_html` по предсказуемым путям
(`/media/.../cp-article-{id}-{position}[_vN].webp`). Если бы «текст» и
«картинки» перегенерировались в любом порядке независимо друг от друга:

- при перегенерации ТЕКСТА модель заново получает список путей картинок,
  которые нужно встроить в разметку (тот же приём, что при первой
  публикации — `image_paths_for` в `_generate_body`);
- но `image_paths_for()` всегда строит пути **версии 1** — это верно только
  для самой первой сборки статьи (картинок ещё не существует, только что
  сгенерированы под эти самые пути). Если картинки уже когда-то
  перегенерировались (например, вчера кто-то нажал «Обложку» → нет, точнее
  «Только внутри»), актуальные файлы лежат под путями `_v2`, `_v3` и т.д., а
  не `_v1`;
- если после этого запустить регенерацию текста, свежий `body_html` сослался
  бы на устаревшую версию 1, а не на актуальную;
- а если внутри одного клика «Всё» после текста ещё выполняется
  регенерация картинок — её механизм замены путей (`old_path_by_position`
  в `regenerate_content_images`) ищет в `body_html` путь **последней
  известной версии по БД** и не находит его в свежем тексте, который
  сослался на версию 1.

Решение — **не независимые операции, а один оркестратор с фиксированным
порядком**: текст (если выбран) обрабатывается всегда первым и явно
запрашивает у БД текущие (последней версии на позицию) пути картинок,
а не строит их с нуля. Картинки и обложка порядка друг с другом не имеют
значения (обложка не встроена в `body_html`).

## 2. `ArticleBuilder` — новые методы

### `_current_content_image_paths() -> list[str]`

Приватный хелпер: для каждой позиции контентной картинки статьи берёт
`remote_path` строки `ArticleImage` с максимальной `version` — тот же
паттерн, что уже используется в `regenerate_content_images` для
`old_path_by_position`. Пустой список, если у статьи вообще нет
контентных картинок (edge case — не должен ронять текст).

### `regenerate_text() -> None`

Перегенерирует заголовок (только отображаемый — slug/URL не
пересчитывается, см. §4), тело и meta уже опубликованной статьи.

1. `_require_synced_reference()` — та же проверка, что и в исходной сборке
   (без эталона рендерить нечего).
2. Рендерит промпт `article_body` с `image_count`/`image_paths`, взятыми из
   `_current_content_image_paths()` (а не из `_image_count()`/
   `image_paths_for()`, которые актуальны только для первой сборки).
3. `text_client.complete_json(prompt)`, запись расхода, проверка на
   `"html" in data`.
4. Применяет результат: `article.title`, `article.body_html`,
   `article.meta_description`, `article.meta_keywords` — **`article.slug`
   не трогается**.
5. Пушит на сайт **одним** PATCH: `site_client.update_page_text(page_id,
   html, title=title, meta_description=..., meta_keywords=...)` — см. §3.
6. `except (LLMError, PromptError, SiteAPIError)`: `error_text` = текст
   ошибки, ничего из уже применённого не откатывается за пределы текущей
   незакоммиченной транзакции (тот же принцип, что в
   `regenerate_content_images`).

Не трогает картинки (ни контентные, ни обложку) и не проверяет их —
пути к текущим картинкам ей нужны только для передачи модели, физические
файлы не создаются и не загружаются.

### `regenerate_cover() -> None`

Перегенерирует обложку страницы (`teaser_image`). В отличие от
контентных картинок обложка не встроена в `body_html` — сайт хранит её
как одно поле страницы, которое `set_page_cover` просто перезаписывает.
Версионирование имени файла (`image_filename(id, 0, version=N)`) нужно
только для истории/аудита стоимости в `ArticleImage`, а не чтобы
сохранить старую обложку видимой — она и так перестаёт быть видимой на
сайте в момент замены поля, независимо от того, как называется файл.

1. Строит промпт `cover` тем же способом, что и `_attach_cover` при первой
   сборке.
2. Генерирует картинку с `COVER_CROP`.
3. `next_version = max(version среди kind="cover") + 1` (или `1`, если
   обложки почему-то нет вовсе — не должно происходить у опубликованной
   статьи, но не должно и ронять метод).
4. `site_client.set_page_cover(remote_page_id, data, filename)`, новая
   строка `ArticleImage(kind="cover", version=next_version, ...)`, запись
   расхода.
5. `except (LLMError, ImageError, SiteAPIError, PromptError)` — тот же
   принцип отчёта об ошибке, что и в остальных частях.

### `regenerate(*, text: bool, images: bool, cover: bool) -> None`

Оркестратор — единственная точка входа для Celery-задачи:

```python
def regenerate(self, *, text: bool, images: bool, cover: bool) -> None:
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

Каждый атомарный метод уже сам коммитит и очищает `regenerating` — это
намеренно (см. §3, почему поле общее): оркестратор просто читает
`article.error_text` сразу после каждого шага (пока его не перезаписал
следующий) и в конце делает один комбинированный `commit()`. Одна упавшая
часть не останавливает остальные — тот же принцип, что уже применяется
для отдельных картинок внутри `regenerate_content_images`.

`regenerate_images_for` (сборка билдера из настроек БД) переименовывается
в `regenerate_article_for(db, article, site, site_client, job_run_id, *,
text, images, cover)` и вызывает `.regenerate(text=, images=, cover=)`
вместо `.regenerate_content_images()`.

## 3. Переименование `images_regenerating` → `regenerating`

Поле защищает от повторного клика и говорит фронту, что статью сейчас
трогает фоновая задача. Раньше оно означало ровно одно: «идёт
перегенерация картинок». Теперь под одним и тем же флагом могут идти три
разных вида работы — оставлять имя `images_regenerating` было бы прямой
ложью для двух из трёх кнопок. Переименовывается сквозно:

- `Article.images_regenerating` → `Article.regenerating` (Alembic
  `alter_column(..., new_column_name=...)` — данные не теряются).
- `ArticleOut.images_regenerating` → `ArticleOut.regenerating`.
- Фронтенд: `ArticleRow.images_regenerating` → `ArticleRow.regenerating`.
- Ровно один флаг на статью — параллельного запуска двух разных частей
  одновременно не бывает: и текст, и картинки, и обложка выбранных частей
  идут **внутри одной Celery-задачи** (см. §2, `regenerate`), а не по
  отдельной задаче на часть. Это же и есть защита от гонки текста и
  картинок за `body_html` из §1 — при одной задаче гонки конкурентных
  воркеров просто не возникает.

## 4. `SiteClient.update_page_text` — необязательные title/meta

```python
def update_page_text(self, page_id: int, html: str, *, title: str | None = None,
                     meta_description: str | None = None,
                     meta_keywords: str | None = None) -> dict:
    payload = {"text": strip_html_comments(html).strip()}
    if title is not None:
        payload["title"] = title
    if meta_description is not None:
        payload["meta_description"] = meta_description
    if meta_keywords is not None:
        payload["meta_keywords"] = meta_keywords
    ...
```

Обратная совместимость полная: `fix_article_image_collision.py`,
`app/companies/builder.py` и `regenerate_content_images` уже вызывают
метод с ровно двумя позиционными аргументами и не передают новые
именованные параметры — их поведение не меняется. `regenerate_text` —
единственный новый вызывающий, которому нужны все четыре поля в одном
PATCH (атомарность: заголовок, meta и текст обновляются на сайте одним
запросом, а не рассинхронизированной парой).

**URL/slug не входит в это PATCH и никогда не меняется при регенерации
текста** — решение по итогам обсуждения дизайна: у API сайта нет метода
переименования URL уже созданной страницы, а самостоятельный пересчёт
`articles_url_prefix + slugify(new_title)` без факта переименования на
сайте рассинхронизировал бы локальный `article.remote_url` с реальным
адресом страницы.

## 5. API и фоновая задача

`POST /api/articles/{id}/regenerate-images` заменяется на:

```
POST /api/articles/{id}/regenerate
{ "text": bool, "images": bool, "cover": bool }
```

- 404 — статьи нет.
- 400 — тело не содержит ни одного `true` (`Field` validator или ручная
  проверка в хендлере).
- 400 — `status != "published"`.
- 400 — `regenerating` уже `True` (тот же анти-race приём, что у
  `run()`/`retry()`: флаг синхронно выставляется в `True` и коммитится до
  `apply_async`).
- Бюджет времени считается по сумме реально выбранных частей, а не всегда
  «по максимуму»:

```python
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

`image_count` — тот же запрос `COUNT(DISTINCT position)` по
`kind="content"`, что и сейчас, и используется только если `images=True`.

Celery-задача `regenerate_article_images`/`regenerate_article_images_sync`
переименовывается в `regenerate_article`/`regenerate_article_sync`,
принимает `article_id` и три булевых флага, вызывает
`regenerate_article_for(..., text=, images=, cover=)`. Структура защиты
(статус должен быть `published`, сайт не должен быть удалён,
`SoftTimeLimitExceeded`/`AIConfigError`/`SecretDecryptionError`) переносится
без изменений — только имена и параметры.

## 6. Frontend

### Страница партии (`BatchPage.tsx`)

Для строк со `status === 'published'` вместо одной кнопки — маленький
заголовок «Перегенерировать картинки» и три компактные кнопки:

| Кнопка | text | images | cover |
|---|---|---|---|
| Только внутри | — | ✓ | — |
| Обложку | — | — | ✓ |
| Все | — | ✓ | ✓ |

Заголовок блока говорит именно «картинки» — здесь текст не
перегенерируется никакой из трёх кнопок, это сознательно (текстовый регон
живёт только в инструменте по ID, см. ниже, чтобы не плодить на и без того
плотной таблице ещё кнопки).

### Страница «Статьи» (`ArticlesPage.tsx`)

Новая кнопка рядом с «Новая партия» открывает модалку: поле «ID статьи»
(число, обязательное) и пять кнопок:

| Кнопка | text | images | cover |
|---|---|---|---|
| Только текст | ✓ | — | — |
| Только картинки внутри | — | ✓ | — |
| Только обложку | — | — | ✓ |
| Все картинки и обложку | — | ✓ | ✓ |
| Все | ✓ | ✓ | ✓ |

Инструмент «выстрелил и забыл»: клик по любой кнопке шлёт `POST
/api/articles/{id}/regenerate` с соответствующим телом, успех/ошибка —
обычным тостом (существующий перехватчик `api.ts` уже показывает текст
ошибки из `detail`). Поле ID не сбрасывается и модалка не закрывается
автоматически — оператору может понадобиться прогнать несколько ID подряд.
Отдельный экран статуса не нужен: прогресс уже виден на странице
конкретной партии через существующий поллинг.

`api.ts`: `regenerateArticleImages(id)` заменяется на
`regenerateArticle(id, parts: { text: boolean; images: boolean; cover: boolean })`,
использующийся из обоих мест.

## 7. Тестирование

- `SiteClient.update_page_text`: новый тест на то, что `title`/
  `meta_description`/`meta_keywords` попадают в PATCH-payload только когда
  переданы (существующие тесты с 2 позиционными аргументами не меняются).
- `ArticleBuilder.regenerate_text`: успешный сценарий (title/body/meta
  обновлены, slug не изменился, `update_page_text` вызван со всеми
  четырьмя полями); использует текущие (не v1) пути картинок — отдельный
  тест с уже перегенерированными на v2 картинками; ошибка LLM/сайта не
  трогает уже сохранённый текст; текст без единой картинки (пустой
  `_current_content_image_paths()`) не падает.
- `ArticleBuilder.regenerate_cover`: успешный сценарий (новая версия,
  `set_page_cover` вызван, старая обложка не удаляется — только новая
  строка `ArticleImage`); ошибка не оставляет `regenerating=True`.
- `ArticleBuilder.regenerate`: каждая комбинация из таблиц выше;
  частичный отказ одной части не останавливает остальные и правильно
  склеивает `error_text`; вызов без единой части выбранной части — ошибка,
  флаг снят.
- `app/tasks.py`: тесты `regenerate_article_images_sync` переименовываются
  и переиспользуются для `regenerate_article_sync` с добавлением
  параметров text/images/cover.
- `app/api/article_batches.py`: 400 на пустой набор частей, 400 на
  неопубликованную статью, 400 на повторный клик, время-бюджет растёт
  только для реально выбранных частей (три отдельных теста на text/images/
  cover своим вкладом в `_regen_time_limits`).
- Frontend: только `tsc && vite build` — модульных тестов на фронте нет,
  как и во всём остальном проекте.

## 8. Открытые вопросы

Нет открытых. Решения по итогам обсуждения 2026-09-09: URL/slug статьи
никогда не меняется при регенерации текста; текст/картинки/обложка
выполняются одной Celery-задачей с фиксированным порядком (текст первым),
а не тремя независимыми; флаг блокировки повторного клика общий для всех
трёх частей и переименован в `regenerating`; интерфейс по ID статьи —
инструмент «выстрелил и забыл» без экрана статуса.
