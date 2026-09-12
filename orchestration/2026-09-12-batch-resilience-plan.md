# Устойчивость партий к сетевым сбоям — план реализации

Дизайн: `directions/2026-09-12-batch-resilience-design.md`
Дата: 2026-09-12

Порядок задач соответствует §11 дизайна: сначала аварийные фиксы (1–3) и
деплой с разблокировкой партии 25 (Task 4), затем видимость и управление (5–8),
затем уборка наследия (9).

## Структура файлов

```
execution/backend/
  app/sites/client.py              Modify: _send(), 9 мест вызова requests.*
  app/tasks.py                      Modify: барьер Exception в 6 *_sync-функциях, celery_task_id в _start_job
  app/models/article.py             Modify: ArticleBatch.run_requested_at
  alembic/versions/<new>_batch_run_requested_at.py   Create: колонка run_requested_at
  app/api/article_batches.py        Modify: batch_runtime_state(), BatchOut, run()
  tests/test_sites_client.py        Modify: +4 теста на сетевые сбои
  tests/test_tasks.py               Modify: +5 тестов на барьер
  tests/test_api_batches.py         Modify: +6 тестов на runtime_state и перезапуск зависшей

execution/frontend/
  src/statuses.ts                   Create: общая карта статусов партии
  src/api.ts                        Modify: Batch.runtime_state, Batch.run_requested_at
  src/pages/ArticlesPage.tsx        Modify: STATUS → импорт из statuses.ts
  src/pages/BatchPage.tsx           Modify: шапка со статусом/прогрессом, алерты, кнопки, поллинг

execution/
  docker-compose.prod.yml           Modify: dns: в x-backend-base
```

## Как запускать

```bash
cd /Users/luzhetskiy/Documents/projects/vibe-coding/k1-content-panel/execution
docker compose run --rm --no-deps backend pytest -q          # тесты без БД (SQLite in-memory)
docker compose up -d postgres redis                           # для проверки миграции
docker compose run --rm backend alembic upgrade head
docker compose run --rm frontend sh -c "npm install && npm run build"
```

---

### Task 1: `SiteClient._send` — сетевые сбои приходят как `SiteAPIError`

**Files:**
- Modify: `execution/backend/app/sites/client.py` (метод `_send` + 9 мест вызова)
- Modify: `execution/backend/tests/test_sites_client.py`

**Не трогать:** докстринг модуля про «клиент ретраи не делает» — он остаётся
верным, ретраи этой задачей не добавляются (см. §4 дизайна). Существующие
тесты подменяют `app.sites.client.requests.get/.post/.patch` — форма `_send(fn,
...)` выбрана именно чтобы их не ломать; если какой-то из них упал, это ошибка
реализации, а не повод править тест.

- [ ] **Step 1: Тесты на обёртку (красные)**

В `tests/test_sites_client.py`:

```python
def test_network_failure_on_get_becomes_site_api_error(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("NameResolutionError: tedwood-uzel.ru")

    monkeypatch.setattr("app.sites.client.requests.get", boom)
    client = SiteClient("https://x.ru", "t")
    with pytest.raises(SiteAPIError) as err:
        client.list_section_pages("/blog/")
    # status_code=None — именно этот признак докстринг SiteAPIError обещает
    # для сетевых сбоев: по нему вызывающий код отличает их от HTTP-ошибок.
    assert err.value.status_code is None
    assert "сеть недоступна" in str(err.value)
    assert "ConnectionError" in str(err.value)
    assert isinstance(err.value.__cause__, requests.ConnectionError)


def test_network_failure_on_post_becomes_site_api_error(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.post",
                        lambda *a, **k: (_ for _ in ()).throw(requests.Timeout("timeout")))
    with pytest.raises(SiteAPIError):
        SiteClient("https://x.ru", "t").create_page("Т", "/blog/t/", "<p>x</p>", 25)


def test_network_failure_on_patch_becomes_site_api_error(monkeypatch):
    monkeypatch.setattr("app.sites.client.requests.patch",
                        lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("x")))
    with pytest.raises(SiteAPIError):
        SiteClient("https://x.ru", "t").update_page_text(7, "<p>x</p>")


def test_mutating_call_is_not_retried_on_network_failure(monkeypatch):
    """Фиксирует ОТСУТСТВИЕ неявных ретраев у мутирующих вызовов: повтор
    create_page после ReadTimeout создал бы вторую страницу на сайте (см.
    §4 дизайна). Тест стоит здесь, чтобы ретраи не «починили» позже, не
    заметив риска дублей."""
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise requests.ConnectionError("x")

    monkeypatch.setattr("app.sites.client.requests.post", boom)
    with pytest.raises(SiteAPIError):
        SiteClient("https://x.ru", "t").create_page("Т", "/blog/t/", "<p>x</p>", 25)
    assert len(calls) == 1
```

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_sites_client.py`
Expected: 4 новых теста падают с `requests.exceptions.ConnectionError`/`Timeout`
(не с `SiteAPIError`) — подтверждение, что сейчас сбой летит наружу голым.

- [ ] **Step 2: `_send` рядом с `_check`**

```python
    def _send(self, fn, url: str, what: str, **kwargs):
        """Единственная точка, через которую клиент ходит в сеть.

        fn — requests.get/post/patch, передаётся аргументом, а не вызывается
        тут по имени через requests.request: существующие тесты подменяют
        app.sites.client.requests.get/.post/.patch, и переход на
        requests.request сломал бы их все сразу.

        Сетевой сбой ниже уровня HTTP (DNS не разрешился, соединение
        отвергнуто, таймаут) приходит как requests.RequestException и
        превращается в SiteAPIError со status_code=None — ровно тот контракт,
        который докстринги SiteAPIError и этого модуля обещают с самого
        начала, но который до сих пор не исполнял никто. Необёрнутый
        ConnectionError 2026-09-03 убил партию 25: он не входит ни в один
        except по пути (ArticleBuilder.build → run_batch_sync), задача Celery
        упала необработанной, и 23 статьи навсегда остались в draft, а партия
        в running (см. directions/2026-09-12-batch-resilience-design.md §1-2).

        Тип исключения попадает в текст: str(ConnectionError) от отказа DNS
        сам по себе малопонятен, а этот текст идёт прямо в Article.error_text
        и показывается менеджеру в таблице партии.

        Ретраев здесь нет сознательно — см. докстринг модуля («сам клиент
        ретраи не делает») и §4 дизайна.
        """
        try:
            response = fn(url, **kwargs)
        except requests.RequestException as exc:
            raise SiteAPIError(f"{what}: сеть недоступна: "
                               f"{type(exc).__name__}: {exc}") from exc
        return self._check(response, what)
```

`SiteAPIError` из `_check` не подкласс `RequestException`, поэтому HTTP-ошибки
этим `except` не перехватываются и текст «сеть недоступна» к ним не приклеится.

- [ ] **Step 3: перевести все 9 мест вызова на `_send`**

Было `self._check(requests.X(url, ...), what)` → стало `self._send(requests.X,
url, what, ...)`. Порядок аргументов: функция, url, `what`, затем остальные
именованные как были.

| Метод | Вызов |
|---|---|
| `list_section_pages` | `self._send(requests.get, f"{self.base_url}{STATICPAGES_PATH}?page={page_number}", "список страниц", headers=self._headers, timeout=self.timeout)` |
| `get_page` | `self._send(requests.get, f"{self.base_url}{STATICPAGES_PATH}{page_id}/", f"страница {page_id}", headers=self._headers, timeout=self.timeout)` |
| `create_page` | `self._send(requests.post, f"{self.base_url}{STATICPAGES_PATH}", "создание страницы", json=payload, headers={**self._headers, "Content-Type": "application/json"}, timeout=self.timeout)` |
| `update_page_text` | `self._send(requests.patch, f"{self.base_url}{STATICPAGES_PATH}{page_id}/", f"обновление страницы {page_id}", json=payload, headers={**self._headers, "Content-Type": "application/json"}, timeout=self.timeout)` |
| `set_page_cover` | `self._send(requests.patch, f"{self.base_url}{STATICPAGES_PATH}{page_id}/", "загрузка обложки", headers=self._headers, files={...}, timeout=self.upload_timeout)` |
| `create_teaser` | `self._send(requests.post, f"{self.base_url}{ADDRESSES_SERVICES_PATH}", "создание тизера", json=payload, headers={...}, timeout=self.timeout)` |
| `update_teaser` | `self._send(requests.patch, f"{self.base_url}{ADDRESSES_SERVICES_PATH}{teaser_id}/", f"обновление тизера {teaser_id}", json=payload, headers={...}, timeout=self.timeout)` |
| `upload_file` | `self._send(requests.post, f"{self.base_url}{FILEMANAGER_PATH}", "загрузка файла", headers=self._headers, files={...}, data={"upload_to": upload_to}, timeout=self.upload_timeout)` |
| `fetch_file` | `self._send(requests.get, absolute, f"файл {url}", timeout=self.timeout)` |

- [ ] **Step 4: проверить, что необёрнутых вызовов не осталось**

Run: `grep -n "requests\.\(get\|post\|patch\|put\|delete\)(" execution/backend/app/sites/client.py`
Expected: пусто — все вхождения теперь передаются в `_send` как `requests.get`
без скобок. (Проверка важна: пропущенное место вызова воспроизведёт исходный
дефект ровно в том же виде.)

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_sites_client.py`
Expected: все тесты зелёные, включая ~20 существующих.

- [ ] **Step 5: полный регресс бэкенда**

Run: `docker compose run --rm --no-deps backend pytest -q`
Expected: зелёно. Отдельно убедиться, что не упали `tests/test_articles_builder.py`
и `tests/test_companies_builder.py` — они ходят через фейковый `site_client`, а не
через `SiteClient`, так что не должны были задеться вовсе.

---

### Task 2: Барьер `except Exception` — статус в БД всегда согласован

**Files:**
- Modify: `execution/backend/app/tasks.py`
- Modify: `execution/backend/tests/test_tasks.py`

**Не трогать** порядок существующих `except`: `SoftTimeLimitExceeded` и
`(AIConfigError, SecretDecryptionError)` обязаны остаться ВЫШЕ нового
`except Exception` — оба их класса его подклассы, и перестановка тихо отключит
уже работающую и покрытую тестами обработку.

- [ ] **Step 1: Тесты (красные)**

```python
def test_run_batch_continues_after_unexpected_exception(db_session, batch, site, monkeypatch):
    """Главный тест задачи: именно этого не хватило 2026-09-03 — необёрнутый
    ConnectionError на второй статье уронил задачу и оставил третью в draft
    навсегда."""
    db_session.add_all([
        Article(batch_id=batch.id, site_id=site.id, topic="А"),
        Article(batch_id=batch.id, site_id=site.id, topic="Б"),
        Article(batch_id=batch.id, site_id=site.id, topic="В"),
    ])
    batch.status = "topics_review"
    db_session.commit()

    def build(db, article, site, site_client, job_run_id):
        if article.topic == "Б":
            raise RuntimeError("соединение оборвалось")
        article.status = "published"

    monkeypatch.setattr("app.tasks.build_for", build)
    monkeypatch.setattr("app.tasks.open_site_client", lambda db, site: SimpleNamespace())

    run_batch_sync(db_session, batch.id)
    db_session.refresh(batch)
    by_topic = {a.topic: a for a in batch.articles}
    assert by_topic["А"].status == "published"
    assert by_topic["Б"].status == "failed"
    assert "RuntimeError" in by_topic["Б"].error_text
    # Третья статья СОБРАНА, а не осталась в draft — суть фикса.
    assert by_topic["В"].status == "published"
    assert batch.status == "done"


def test_run_batch_unexpected_exception_outside_loop_marks_batch_failed(
        db_session, batch, site, monkeypatch):
    db_session.add(Article(batch_id=batch.id, site_id=site.id, topic="А"))
    batch.status = "topics_review"
    db_session.commit()

    def broken(db, site):
        raise RuntimeError("брокер секретов недоступен")

    monkeypatch.setattr("app.tasks.open_site_client", broken)

    # Перевыброс обязателен: в БД состояние согласовано, но Celery должна
    # увидеть FAILURE и положить трейсбек в лог воркера, а не SUCCESS.
    with pytest.raises(RuntimeError):
        run_batch_sync(db_session, batch.id)
    db_session.refresh(batch)
    assert batch.status == "failed"
    assert "RuntimeError" in batch.error_text
    job = db_session.query(JobRun).order_by(JobRun.id.desc()).first()
    assert job.status == "failed"
    assert job.finished_at is not None
```

Плюс по одному тесту того же вида на `retry_article_sync`,
`generate_topics_sync` и `run_company_batch_sync` (для последней — что третья
компания собрана).

Run: `docker compose run --rm --no-deps backend pytest -q tests/test_tasks.py`
Expected: новые тесты падают — `RuntimeError` вылетает из `run_batch_sync`
наружу, партия остаётся `running`, джоба `running`.

- [ ] **Step 2: внутренний барьер в `run_batch_sync`**

```python
        for article in batch.articles:
            if article.status == "published":
                continue
            try:
                build_for(db, article, site, site_client, job.id)
            except (SoftTimeLimitExceeded, AIConfigError, SecretDecryptionError):
                # Лимит времени и ошибка конфигурации — общие для всей партии,
                # их обрабатывают внешние except ниже (и обрывают партию
                # целиком). Перевыброс, а не обработка здесь: семантика,
                # описанная в комментариях к ним, не меняется.
                raise
            except Exception as exc:  # noqa: BLE001
                # Билдер сам ловит свои типы (LLMError/ImageError/SiteAPIError/
                # PromptError/ArticleBuildError) и наружу их не отдаёт. Всё
                # остальное до 2026-09-03 роняло задачу целиком: партия 25
                # потеряла 23 статьи из-за одного ConnectionError (Task 1
                # закрывает тот конкретный тип, этот барьер — весь класс).
                # rollback обязателен: транзакция могла остаться незавершённой
                # (тот же довод, что в ArticleBuilder.build()).
                db.rollback()
                article.status = "failed"
                article.error_text = (f"непредвиденная ошибка: "
                                      f"{type(exc).__name__}: {exc}")
            db.commit()
```

- [ ] **Step 3: внешний барьер в `run_batch_sync`**

После существующего `except (AIConfigError, SecretDecryptionError)`:

```python
    except Exception as exc:  # noqa: BLE001
        # Сбой вне цикла по статьям (open_site_client, обращение к
        # batch.articles на мёртвом соединении). Порядок важен: этот except
        # стоит последним, иначе он перехватил бы SoftTimeLimitExceeded и
        # AIConfigError — оба его подклассы.
        db.rollback()
        done = len([a for a in batch.articles if a.status == "published"])
        batch.status = "failed"
        batch.error_text = (f"непредвиденная ошибка: {type(exc).__name__}: {exc}; "
                            f"готово {done}/{len(batch.articles)}")
        db.commit()
        _finish_job(db, job, "failed", batch.error_text)
        raise
```

`raise` в конце — отличие от соседних обработчиков, и оно осознанное: таймаут
и ошибка конфигурации ожидаемы и полностью описаны текстом в UI, а
непредвиденное исключение нужно видеть трейсбеком в логах воркера.

- [ ] **Step 4: то же в `run_company_batch_sync`** (внутренний + внешний,
      дословно тот же приём, `company` вместо `article`).

- [ ] **Step 5: внешний барьер в остальных четырёх**

`generate_topics_sync`, `retry_article_sync`, `regenerate_article_images_sync`,
`retry_company_sync` — по одному `except Exception as exc` в конце, повторяющему
форму соседнего обработчика этой функции (что она пишет в статус и что в
`error_text`), плюс `raise`. В `regenerate_article_images_sync` статус статьи
НЕ трогать — там это запрещено докстрингом функции, снимается только
`images_regenerating` и пишется `error_text`.

- [ ] **Step 6: мутационная проверка**

Убрать `except Exception` из цикла `run_batch_sync`, прогнать
`tests/test_tasks.py`.
Expected: падает именно `test_run_batch_continues_after_unexpected_exception`.
Вернуть код. (Приём из `plan1-execution-workflow`: тест, который не ловит
снятие защиты, не считается покрытием.)

- [ ] **Step 7: полный регресс**

Run: `docker compose run --rm --no-deps backend pytest -q`
Expected: зелёно.

---

### Task 3: DNS контейнеров мимо systemd-resolved

**Files:**
- Modify: `execution/docker-compose.prod.yml` (якорь `x-backend-base`)

- [ ] **Step 1** В `x-backend-base` добавить `dns: ["77.88.8.8", "1.1.1.1"]`
      с комментарием: цепочка «контейнер → 127.0.0.11 → 127.0.0.53
      (systemd-resolved) → внешний» падала на втором хопе 3, 4, 7, 8 и 10
      сентября; `dns:` убирает этот хоп. Якорь общий для `api`, `worker` и
      `migrate`.
- [ ] **Step 2** Проверить, что dev-compose не задет (`docker-compose.yml`
      отдельный файл, правка только прод).

Run на проде после деплоя: `docker exec execution-worker-1 cat /etc/resolv.conf`
Expected: `nameserver 127.0.0.11`, но в `ExtServers` — указанные адреса вместо
`host(127.0.0.53)`.

---

### Task 4: Деплой и разблокировка партии 25

Операционная задача, не код. Выполняется только после зелёного регресса
Task 1–3.

- [ ] **Step 1** Коммит, пуш, деплой. После деплоя обязательно проверить
      `curl https://content-panel.nastroyker.ru/api/health`; при 502 —
      `ssh k1-panel-vps "docker exec execution-frontend-1 nginx -s reload"`
      (известная ловушка кэша nginx, см. память проекта).
- [ ] **Step 2** Проверить DNS в контейнере (Task 3, Step 2).
- [ ] **Step 3** Привести партию 25 в согласованное состояние: джоба 140 →
      `failed` с текстом про оборванную задачу, статья 232 → `failed`, партия
      25 → `failed`. До Task 7 это делается SQL, после — кнопкой.
- [ ] **Step 4** `POST /api/article-batches/25/run` — дособрать 24 статьи
      (темы целы, повторный подбор не нужен; уже опубликованные 25 статей
      пропустит существующая проверка `status == "published"`).
- [ ] **Step 5** Следить за `/jobs` и за `llm_usage` по новой джобе. Ожидаемый
      расход — порядка 1000 единиц, время — около 2 часов.

---

### Task 5: `run_requested_at` + `celery_task_id`

**Files:**
- Modify: `execution/backend/app/models/article.py` (`ArticleBatch.run_requested_at`)
- Create: `execution/backend/alembic/versions/<generated>_batch_run_requested_at.py`
- Modify: `execution/backend/app/tasks.py` (`_start_job` пишет `celery_task_id`)
- Modify: `execution/backend/tests/test_models_article.py`, `tests/test_tasks.py`

- [ ] Step 1: тест, что новая партия имеет `run_requested_at is None`, а после
      `run()` — заполненный.
- [ ] Step 2: колонка `run_requested_at` (nullable `DateTime(timezone=True)`)
      с комментарием, почему не годится `created_at` (создание партии может
      быть на дни раньше запуска — партия 25: создана 11:04, запущена 11:24,
      но в общем случае разрыв произвольный).
- [ ] Step 3: миграция; применить на живом Postgres, а не только на SQLite.
- [ ] Step 4: `_start_job` пишет `celery_task_id` из `current_task.request.id`
      (пусто, когда функция вызвана напрямую из теста — не падать).

---

### Task 6: `batch_runtime_state`

**Files:**
- Modify: `execution/backend/app/api/article_batches.py`
- Modify: `execution/backend/tests/test_api_batches.py`

- [ ] Step 1: по тесту на каждое из пяти правил таблицы §6.1 дизайна, включая
      «джоба завершена, а партия всё ещё running» (это состояние реально есть
      в проде: партия 10 `done` при джобе 29 `running`).
- [ ] Step 2: реализация. Лимит — из существующей
      `_batch_time_limits(len(batch.articles))[1]`, без второго источника правды.
      Джоба ищется по `JobRun.kind == 'run_batch'` и `params_json['batch_id']`.
- [ ] Step 3: проверить JSON-запрос на живом Postgres (JSONB), а не только на
      SQLite — это единственное место плана, где диалекты расходятся.
- [ ] Step 4: `runtime_state` и `run_requested_at` в `BatchOut` + `_to_out`.

---

### Task 7: `run()` перезапускает зависшую партию

**Files:**
- Modify: `execution/backend/app/api/article_batches.py`
- Modify: `execution/backend/tests/test_api_batches.py`

- [ ] Step 1: тесты — зависшая партия перезапускается и её `generating`-статьи
      становятся `failed`; `working` даёт 400 «уже выполняется»; `queued` даёт
      400 «ждёт свободный воркер»; зависшая джоба помечается `failed`.
- [ ] Step 2: реализация в ветке `if batch.status == "running"`; `run_requested_at`
      выставляется при каждом запуске.
- [ ] Step 3: убедиться, что `test_run_twice_dispatches_once` (защита от
      двойного клика) по-прежнему зелёный — новая ветка не должна её ослабить.

---

### Task 8: Frontend страницы партии

**Files:**
- Create: `execution/frontend/src/statuses.ts`
- Modify: `execution/frontend/src/api.ts`, `src/pages/ArticlesPage.tsx`,
  `src/pages/BatchPage.tsx`

- [ ] Step 1: вынести карту статусов партии в `statuses.ts`, `ArticlesPage`
      импортирует её оттуда (подписи на двух экранах не должны разойтись).
- [ ] Step 2: шапка страницы партии — тег статуса + «готово N из M»
      (считается по уже приходящему списку `articles`).
- [ ] Step 3: алерт «Не удалось подобрать темы» только когда статей нет;
      иначе «Сборка прервалась».
- [ ] Step 4: `runtime_state === 'stuck'` → предупреждение с кнопкой
      «Дособрать партию»; `'queued'` → «Ждёт свободного воркера».
- [ ] Step 5: кнопка «Дособрать партию» также для `done`/`failed` при наличии
      статей не в `published`.
- [ ] Step 6: кнопка повтора в строке — и для `draft`.
- [ ] Step 7: поллинг останавливается при `stuck`.
- [ ] Step 8: `npm run build` (tsc + vite) зелёный.

---

### Task 9: Уборка наследия

- [ ] Зависшие джобы: 140 и 29 (`run_batch`), 183 (`retry_company`) → `failed`
      с текстом про оборванную задачу.
- [ ] Компания 292 — в `generating` с 4 сентября по той же причине (DNS по
      `stroybaza-kaluga.ru`): → `failed`, затем повтор кнопкой.
- [ ] Шесть статей в `draft` вне партии 25 — разобрать по партиям: дособрать
      или признать неактуальными.

## Итоговая проверка

- [ ] `docker compose run --rm --no-deps backend pytest -q` — зелёно.
- [ ] `docker compose run --rm frontend sh -c "npm install && npm run build"` — зелёно.
- [ ] `grep -n "requests\.\(get\|post\|patch\)(" app/sites/client.py` — пусто.
- [ ] Миграции применяются с нуля на живом Postgres.
- [ ] На проде: партия 25 собрана целиком (49/49), `job_runs` без записей
      `running` старше часа, `articles` без `draft` и `generating` в партии 25.
