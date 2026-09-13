---
name: running-local-panel
description: Use before claiming that a change to the k1-content-panel panel works — an endpoint, a screen, a status that appears only under a particular state in the database. Also use when a defect needs reproducing on a live stand rather than in tests.
---

# Запуск локального стенда панели

Поднимает `execution/` (FastAPI + Celery + Postgres + Redis + Vite) и доводит до
состояния, в котором можно дёргать API под реальной сессией и открывать экраны.

**Зачем:** тесты не ловят дефекты, живущие в связке «состояние в БД → ответ API →
условия отрисовки». Один такой стоил бы двойной оплаты партии — нашёлся именно
прогоном сценария на стенде (см. `orchestration/2026-09-12-batch-resilience-plan.md`).

Все команды — из `execution/`.

## 1. Поднять

**Сначала посмотреть, что уже поднято** — на этой машине контейнеры залёживаются
месяцами:

```bash
docker compose ps --format '{{.Name}}\t{{.State}}\t{{.Status}}'
```

Проверено 2026-09-13: `worker`, `frontend` и `backend` висели с 13 августа. Так
что «я его не запускал» — не довод, состояние надо читать, а не предполагать.

```bash
docker compose up -d api        # тянет за собой postgres, redis и migrate
until curl -sf -m 3 http://127.0.0.1:8000/api/health >/dev/null; do sleep 1; done
```

`--env-file` локально НЕ нужен: dev-compose имеет дефолты на все секреты.
Это отличие от прода, где он обязателен в каждой команде (см. `DEPLOY.md`).

**Воркер: решить осознанно, а не по умолчанию.** Команда выше его не поднимает,
и это удобно — поставленные задачи Celery лежат в очереди, можно проверять
постановку и смену статусов, не тратя деньги RouterAI. Но оставшийся с прошлого
раза воркер ЗАБЕРЁТ задачу молча.

Так и случилось 2026-09-13: августовский контейнер подобрал обе тестовые
`run_batch` и уронил их на `column articles.images_regenerating does not exist` —
он крутил код месячной давности, а схема БД была свежая. Денег это не стоило
только потому, что он умер на первом же запросе к БД, до любого вызова RouterAI.
С кодом посвежее он честно пошёл бы генерировать за деньги.

Отсюда правило: перед постановкой задач посмотреть не только «запущен ли», но и
«какой код внутри».

```bash
docker compose ps worker                       # есть ли вообще
docker logs execution-worker-1 2>&1 | tail -3  # жив ли он и что делает
```

`Cannot connect to redis://redis:6379/0` — воркер отключён и безвреден, но
оживёт сам, как только поднимется redis. Не нужен — `docker compose stop worker`.
Нужен — пересоздать (`docker compose up -d --build worker`), иначе он работает
не тем кодом, который проверяется.

Фронт (нужен только для экранов): `docker compose up -d frontend` →
http://127.0.0.1:3000, проксирует на api. Vite отдаёт код через bind-mount, так
что правки видны без пересборки — но зависимости у давно живущего контейнера
августовские: если добавлялся новый пакет, контейнер надо перезапустить
(`docker compose restart frontend`), иначе импорт упадёт.

## 2. Администратор

`create_admin.py` интерактивен и для скрипта не годится:

```bash
docker compose run --rm -T backend python <<'PY'
from sqlalchemy import select
from app.api.security import hash_password
from app.db import SessionLocal
from app.models.user import User
db = SessionLocal()
if not db.scalar(select(User).where(User.email == "test@local")):
    db.add(User(email="test@local", full_name="Тест", role="admin",
                password_hash=hash_password("test12345"), is_active=True))
    db.commit()
print("админ готов")
PY
```

## 3. Войти

Логин принимает **form-data** (`OAuth2PasswordRequestForm`), поле называется
`username`, а не `email`. JSON даёт 422 — это первая ловушка:

```bash
curl -s -c /tmp/cookies.txt -X POST http://127.0.0.1:8000/api/auth/login \
     -d 'username=test@local&password=test12345' -w '%{http_code}\n' -o /dev/null
curl -s -b /tmp/cookies.txt http://127.0.0.1:8000/api/article-batches | head -c 400
```

## 4. Завести сценарий

Состояния, которые не собрать через UI (зависшая партия, оборванная джоба),
пишутся прямо в БД тем же `docker compose run --rm -T backend python <<'PY'`.
Нужны `Site` (иначе партию не к чему привязать), затем `ArticleBatch` + `Article`
+ при необходимости `JobRun`. Готовый пример зависшей партии — в истории
коммита `99fc45d`.

## 5. Прогнать и убрать за собой

Дёрнуть эндпоинт, который трогали, и прочитать тело ответа — не только код.
Затем удалить заведённые строки и:

```bash
docker compose stop api postgres redis frontend
```

## Быстрая справка

| Задача | Команда |
|---|---|
| Тесты (без БД) | `docker compose run --rm --no-deps backend pytest -q` |
| Миграции на живом Postgres | `docker compose up -d postgres && docker compose run --rm backend alembic upgrade head` |
| Сборка фронта (tsc) | `docker compose run --rm --no-deps frontend sh -c "npm install && npm run build"` |
| Разовый python в контейнере | `docker compose run --rm -T backend python <<'PY' ... PY` |

## Частые ошибки

| Симптом | Причина |
|---|---|
| Логин отдаёт 422 | Послан JSON с `email`; нужен form-data с `username` |
| Задача не выполняется | Воркер не поднят — по умолчанию так и задумано |
| Задача ВЫПОЛНИЛАСЬ, хотя не ждали | Воркер остался живым с прошлого раза; это стоит денег |
| `alembic` не видит БД | `--no-deps` отрезает postgres; для миграций он нужен |
| Изменения фронта не видны | Открыт :8000 вместо :3000 |
| Падает импорт нового пакета на фронте | Контейнер поднят до его появления — `docker compose restart frontend` |
