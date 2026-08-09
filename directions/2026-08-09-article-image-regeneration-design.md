# Перегенерация картинок в готовой статье — дизайн

Дата: 2026-08-09
Статус: на утверждение
Базовые документы: `execution/backend/app/articles/builder.py`,
`execution/backend/app/models/article.py`, `execution/backend/app/sites/client.py`,
`execution/backend/app/tasks.py`, `execution/backend/app/api/article_batches.py`,
`execution/frontend/src/pages/BatchPage.tsx`

На странице партии, для каждой уже опубликованной статьи (`status = published`),
добавляется кнопка «Перегенерировать картинки»: она заново генерирует **только
картинки, встроенные в текст статьи** (`content`-картинки внутри `body_html`),
заливает их под новым именем файла и обновляет пути в тексте статьи на сайте.
Обложка страницы (`teaser_image`) не трогается — это отдельный от текста механизм.
Старые файлы картинок и старые строки `ArticleImage` не удаляются.

## 1. Почему нельзя просто перезалить картинку под тем же именем

`SiteClient.upload_file` (`execution/backend/app/sites/client.py`) заливает файл в
filemanager сайта и при совпадении имени молча перезаписывает существующий файл —
именно так уже случался инцидент с коллизией `article_4/5/6` (см. докстринг
`image_filename` в `builder.py`). Требование «старые картинки не удалять» означает,
что новая генерация обязана получить **новое** имя файла, а не переиспользовать
старое.

## 2. Именование файлов и версии

`image_filename(article_id, position, version=1)` (`builder.py`) расширяется
необязательным параметром `version`:

```
version == 1  → cp-article-{id}-{position}.webp        (как сейчас, без изменений)
version >  1  → cp-article-{id}-{position}_v{version}.webp
```

Все существующие вызовы (`build_for`, `_upload_content_images`, `_attach_cover`) не
передают `version` и продолжают получать прежние имена — обратная совместимость
полная, старые тесты не меняются.

`ArticleImage` получает колонку `version: int, default=1` (миграция Alembic,
существующие строки получают `1`). Один клик «перегенерировать» — это один раунд:
все позиции, которые перегенерировались в этом раунде, получают один и тот же
номер версии, `next_version = max(version по content-картинкам статьи) + 1`.

Позиции берутся не из текущей настройки сайта (`site.reference_images` могла
измениться после публикации статьи), а из фактически существующих
`ArticleImage(kind="content")` этой статьи — так перегенерация всегда бьёт ровно по
тем картинкам, что реально вставлены в `body_html`.

## 3. Замена путей в тексте

Для каждой позиции берётся `remote_path` картинки с максимальной версией **этой
конкретной позиции** (на случай, если предыдущий раунд перегенерации был частичным
и версии разных позиций разошлись) — это и есть «старый путь». После успешной
генерации новой картинки для этой позиции в `article.body_html` делается точечная
замена строки: старый путь → новый путь (`str.replace`, пути — предсказуемые,
уникальные строки вида `/media/{ARTICLE_IMG_DIR}cp-article-{id}-{position}...webp`,
коллизий по построению не бывает).

Если для позиции генерация не удалась — её путь в `body_html` не трогается, старая
картинка продолжает отображаться.

## 4. Публикация изменений на сайте

Статья уже опубликована черновиком (`remote_page_id` есть). После того как
`body_html` изменился хотя бы для одной позиции, вызывается уже существующий
`SiteClient.update_page_text(page_id, html)` — тот же метод, которым раньше вручную
чинили коллизию имён картинок. Обложка страницы не трогается: `set_page_cover` в
этом потоке не вызывается.

## 5. Модель данных — изменения

| Таблица | Изменение |
|---|---|
| `article_images` | `+ version: int, default=1` |
| `articles` | `+ images_regenerating: bool, default=false` |

`images_regenerating` — не подменяет `status` статьи (он остаётся `published`),
а служит отдельным флагом «идёт фоновая перегенерация картинок» — и для защиты от
повторного клика, и как сигнал фронту, что нужно продолжать опрашивать партию.

Отказ (полный или частичный) не переводит статью в `failed` — `status` остаётся
`published` (страница на сайте существует и работает), а причина отказа/сводка
пишется в уже существующее поле `error_text` (у опубликованной статьи оно обычно
пустое, так что переиспользование не конфликтует с исходной семантикой поля и
переиспользует существующий UI — expandable-строка в таблице уже показывает
`error_text`, если он непустой).

## 6. Backend — поток выполнения

Новая пара функций в `app/tasks.py`, по образцу `retry_article`/`retry_article_sync`:

```
regenerate_article_images_sync(db, article_id):
    article = db.get(Article, article_id)
    if article.status != "published": → выход (гонка, эндпоинт уже проверил)
    site = db.get(Site, article.site_id); если None → error_text, images_regenerating=False, выход
    site_client = open_site_client(db, site)

    content_images = [i for i in article.images if i.kind == "content"]
    positions = sorted({i.position for i in content_images})
    if not positions: → error_text="нет картинок для перегенерации", выход

    next_version = max(i.version for i in content_images) + 1
    old_path_by_position = {
        position: max((i for i in content_images if i.position == position),
                      key=lambda i: i.version).remote_path
        for position in positions
    }

    # промпты — последовательно (как в builder._generate_content_images),
    # генерация картинок — параллельно (ThreadPoolExecutor), тот же паттерн
    # "ждать все futures, копить первую ошибку, не терять оплаченные результаты"
    for каждая успешная (position, image_bytes, prompt, cost):
        filename = image_filename(article.id, position, version=next_version)
        path = site_client.upload_file(image_bytes, filename, ARTICLE_IMG_DIR)
        db.add(ArticleImage(article_id=article.id, kind="content", position=position,
                            version=next_version, prompt=prompt, remote_path=path, cost=cost))
        article.body_html = article.body_html.replace(old_path_by_position[position], path)
        record_usage(...)
        db.commit()

    if изменился хотя бы один путь:
        site_client.update_page_text(article.remote_page_id, article.body_html)
        db.commit()

    if были ошибки: article.error_text = "перегенерировано K/N, ошибка: ..."
    else: article.error_text = ""
    article.images_regenerating = False
    db.commit()
```

Обёртки по образцу остальных задач: `SoftTimeLimitExceeded`,
`AIConfigError`/`SecretDecryptionError` ловятся отдельно, всегда сбрасывают
`images_regenerating = False` и пишут причину в `error_text`, не трогая уже
применённые изменения `body_html`/`ArticleImage` (они уже закоммичены построчно
внутри цикла, симметрично `_generate_content_images` в `builder.py`).

Расход пишется в `LlmUsage` под новым `JobRun(kind="regenerate_article_images")` —
так же, как остальные операции пишут в журнал расходов.

## 7. API

`POST /api/articles/{article_id}/regenerate-images`:

- 404, если статьи нет.
- 400, если `status != "published"` или `images_regenerating` уже `True`.
- Синхронно (в теле эндпоинта, до `apply_async`) выставляет
  `images_regenerating = True` и коммитит — тот же приём анти-гонки, что уже
  применён в `run()` и `retry()` (`app/api/article_batches.py`).
- Считает бюджет времени задачи по числу картинок статьи (по аналогии с
  `_retry_time_limits`, но без стоимости тела статьи и обложки):
  `soft = REGEN_FIXED_SECONDS + REGEN_PER_IMAGE_SECONDS * len(positions)`.
- Ставит `regenerate_article_images.apply_async(args=[article.id], ...)`.
- Отвечает `{"ok": true}`.

`ArticleOut` получает поле `images_regenerating: bool`.

## 8. Frontend

`BatchPage.tsx`, таблица статей (нередактируемый режим):

- Новая колонка-кнопка: для строк с `status === 'published'` — кнопка
  «Перегенерировать картинки» (`Popconfirm`, как у существующего retry), иконка
  меняется на спиннер и кнопка блокируется, пока `r.images_regenerating`.
- `useEffect` с автообновлением партии (сейчас триггерится на
  `topics_pending`/`running`/`generating`) расширяется условием
  `batch.articles.some(a => a.images_regenerating)`, чтобы таблица сама обновилась,
  когда фон закончит работу.
- `api.ts`: `regenerateArticleImages(articleId)` → `POST
  /api/articles/{id}/regenerate-images`; `ArticleRow` получает
  `images_regenerating: boolean`.

## 9. Тестирование

- `image_filename`: `version=1` не меняет имя (регресс на существующие тесты),
  `version=2` даёт `_v2` перед расширением.
- Backend sync-функция (тесты по образцу `test_tasks.py`, RouterAI/site-клиент
  замоканы): успешная перегенерация всех позиций — новые `ArticleImage` с
  `version=2`, старые строки не удалены, `body_html` содержит новые пути,
  `update_page_text` вызван с обновлённым html; частичный отказ — только
  успешные позиции заменены в тексте, `error_text` содержит сводку,
  `images_regenerating` сброшен в обоих случаях.
- API: 400 при `status != published`, 400 при повторном вызове пока
  `images_regenerating = True`, 404 на несуществующую статью.
- Гонка двойного клика: два быстрых `POST .../regenerate-images` подряд — в
  очередь уходит одна задача (по аналогии с `test_run_twice_dispatches_once`).

## 10. Открытые вопросы

Нет открытых. Решения по итогам обсуждения 2026-08-09: перегенерация — только
контентные картинки в тексте, обложка не трогается; кнопка доступна только для
`status = published`; выполнение — асинхронно через Celery с полем
`images_regenerating` для поллинга на фронте; старые файлы и записи `ArticleImage`
не удаляются никогда.
