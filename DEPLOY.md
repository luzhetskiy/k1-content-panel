# Воркфлоу: git → сервер → деплой

Документ описывает, как устроен прод и что делать руками. Деплой ручной —
автодеплой пока не нужен.

## Что где

| | |
|---|---|
| Репозиторий | `git@github.com:luzhetskiy/k1-content-panel.git` (приватный) |
| Сервер | `77.222.55.36`, Ubuntu |
| Домен | https://content-panel.nastroyker.ru |
| Код на сервере | `/home/panel/k1-content-panel` |
| Рабочий пользователь | `panel` (в группах `sudo`, `docker`) |

## Доступ

Ключ `~/.ssh/id_ed25519_github` прописан и root, и `panel`. В `~/.ssh/config`
заведены алиасы:

```bash
ssh k1-panel-vps        # panel@77.222.55.36 — повседневная работа
ssh k1-panel-vps-root   # root — только системные вещи (nginx, certbot, apt)
```

RSA-ключ (`id_rsa`) сервер может не принимать на новых версиях OpenSSH —
используется только ed25519.

## Слои: кто кого слушает

```
интернет :443
   └─ nginx на хосте (TLS, сертификат Let's Encrypt)
        └─ 127.0.0.1:8080 → контейнер frontend (nginx + собранная статика)
             ├─ /        → статика React (SPA-роутинг)
             └─ /api/    → контейнер api:8000 (uvicorn)
                              ├─ postgres:5432   (только внутри сети docker)
                              └─ redis:6379      (только внутри сети docker)
                                   ↑
                              worker (celery) — тот же образ, другая команда
```

Маршрутизация `/api/` живёт **только** во `frontend/nginx.conf` (включая
`limit_req` на `/api/auth/login`). Хостовый nginx проксирует весь трафик
на `127.0.0.1:8080` целиком — иначе правило пришлось бы синхронно править
в двух местах.

Порты postgres и redis наружу не публикуются. В dev-конфиге
(`docker-compose.yml`) они висят на `127.0.0.1` — это осознанно оставлено
для локальной разработки и **не должно** переезжать в прод-файл.

## Обычный цикл разработки

```bash
# локально
git checkout -b feature/что-делаем
# ... правки, тесты ...
cd execution/backend && docker compose run --rm backend python -m pytest -q
cd ../frontend && npm run build          # должно быть 0 ошибок TypeScript

git add -A && git commit -m "feat: ..."
git checkout main && git merge feature/что-делаем
git push origin main
git branch -d feature/что-делаем
```

Прямой коммит в `main` допустим для мелочей, но ветка + merge оставляет
внятную историю.

## Деплой (ручной)

```bash
ssh k1-panel-vps
cd ~/k1-content-panel/execution
git pull origin main

docker compose -f docker-compose.prod.yml up -d --build

# миграции применяет сервис migrate — он отрабатывает до api/worker
docker compose -f docker-compose.prod.yml ps
```

`--build` обязателен: образы собираются из исходников, bind-mount'ов в
проде нет, без пересборки контейнеры поднимутся на старом коде.

Проверка после деплоя:

```bash
curl -s https://content-panel.nastroyker.ru/api/health   # {"status":"ok"}
docker compose -f docker-compose.prod.yml logs --tail=50 api

# Воркер обязан перечислить задачи generate_topics/run_batch/retry_article.
# Пустой список или строки `Received unregistered task of type ...` означают,
# что задачи уходят в никуда и конвейер молча стоит, хотя health отвечает 200.
docker compose -f docker-compose.prod.yml logs worker | grep -A6 '\[tasks\]'
docker compose -f docker-compose.prod.yml logs worker | grep -c unregistered   # 0
```

Откат — на предыдущий коммит, той же командой:

```bash
git log --oneline -5
git checkout <sha>
docker compose -f docker-compose.prod.yml up -d --build
```

Схему БД откат назад не отменяет: если релиз содержал миграцию, откатывать
её нужно отдельно (`alembic downgrade -1`) и осознанно.

## Первичная установка с нуля

Уже выполнено, здесь — для воспроизводимости.

```bash
# 1. Пользователь и ключи (от root)
useradd -m -s /bin/bash panel && usermod -aG sudo,docker panel
# ~/.ssh/authorized_keys для root и panel = ваш ed25519

# 2. Пакеты (от root)
apt-get update && apt-get install -y nginx certbot python3-certbot-nginx \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 3. Firewall
ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw --force enable

# 4. Deploy-ключ для приватного репозитория (от panel)
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_deploy -N ""
# публичную часть добавить в GitHub → Settings → Deploy keys (read-only)

# 5. Код и окружение
git clone git@github.com:luzhetskiy/k1-content-panel.git ~/k1-content-panel
cd ~/k1-content-panel/execution
cp .env.prod.example .env.prod && chmod 600 .env.prod    # заполнить значения!

# 6. nginx + TLS (от root)
cat > /etc/nginx/sites-available/content-panel <<'EOF'
server {
    listen 80;
    server_name content-panel.nastroyker.ru;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 600s;
    }
}
EOF
ln -s /etc/nginx/sites-available/content-panel /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d content-panel.nastroyker.ru

# 7. Запуск и первый администратор
cd ~/k1-content-panel/execution
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.prod run --rm api python create_admin.py
```

После этого зайти на https://content-panel.nastroyker.ru и **завести карточки
сайтов и промпты** («Сайты», «Промпты», «Настройки» в админке) — без токена
API целевого сайта и ключа RouterAI генерация партий работать не будет,
сервис стартует пустым по замыслу.

## Секреты

`execution/.env.prod` на сервере (в `.gitignore`, права 600) содержит три
значения:

| Переменная | Что ломается при потере |
|---|---|
| `DB_PASSWORD` | доступ к БД; меняется только через `ALTER USER`, переменной уже инициализированную базу не перенастроить |
| `JWT_SECRET` | смена = разлогин всех; **пустое значение недопустимо** — форма `${VAR:?...}` в `docker-compose.prod.yml` роняет запуск, а не подставляет пустую строку |
| `ENCRYPTION_KEY` | безвозвратно: токены сайтов и ключ RouterAI перестают расшифровываться, «Настройки» и карточки сайтов покажут ошибку расшифровки (Task 5/25) |

`ENCRYPTION_KEY` хранить в бэкапе **отдельно** от дампа базы — вместе они
дают полный доступ к токенам целевых сайтов и RouterAI.

## Эксплуатация

```bash
cd ~/k1-content-panel/execution
C="docker compose -f docker-compose.prod.yml --env-file .env.prod"

$C ps                          # статус
$C logs -f --tail=100 worker   # логи генерации
$C logs -f --tail=100 api      # логи API
$C restart api                 # перезапуск одного сервиса
$C exec postgres psql -U app -d content   # консоль БД

# бэкап базы
$C exec -T postgres pg_dump -U app content | gzip > ~/backup-$(date +%F).sql.gz

# место на диске (образы копятся при каждом --build)
docker system df && docker image prune -f
```

Восстановление из бэкапа:

```bash
$C stop api worker
gunzip -c ~/backup-2026-08-06.sql.gz | $C exec -T postgres psql -U app -d content
$C start api worker
```

Сертификат Let's Encrypt продлевается таймером `certbot.timer`
автоматически; проверить — `systemctl list-timers certbot.timer`.

## Типовые неполадки

- **«Не удалось расшифровать» на странице настроек или в карточке сайта** —
  `ENCRYPTION_KEY` в `.env.prod` не совпадает с тем, которым секрет был
  зашифрован при сохранении (например, ключ случайно перегенерировали при
  редеплое). Поле нужно ввести заново поверх текущего ключа — старое
  значение восстановить нельзя, только заново запросить токен/ключ у
  источника (Task 5, `SettingsService`, `admin_sites.py`).
- **Worker ничего не обрабатывает, партии зависают в `topics_pending`/
  `running`** — проверить, что контейнер `worker` вообще запущен
  (`$C ps`) и что он видит задачи (`$C logs worker | grep -A6 '\[tasks\]'`).
  Частая причина — `worker` не пересобрался при деплое (забыли `--build`)
  и работает на старом образе без новых задач.
- **403 от API целевого сайта** (`SiteAPIError`, кнопка «Проверить и
  синхронизировать» или публикация статьи) — токен сайта просрочен/отозван
  или не совпадает с `articles_parent_id`/`reference_article_id` в карточке.
  Обновить токен и/или id в «Сайты» → «Правка» и повторить синхронизацию.

## Открытые риски

- **Вход по паролю на SSH оставлен включённым по вашему решению**, при том
  что root-пароль передавался открытым текстом в переписке. Пока это так,
  сервер защищён ровно этим паролем. Закрывается двумя строками в
  `/etc/ssh/sshd_config` (`PasswordAuthentication no`,
  `PermitRootLogin prohibit-password`) — ключи уже настроены и проверены,
  доступ не потеряется.
- **Бэкапы не автоматизированы** — команда выше запускается руками.
- **Алертов нет.**

## Задел под автодеплой

Ручной цикл выше сводится к трём командам (`git pull`, `up -d --build`,
проверка `/api/health`), поэтому GitHub Actions по push в `main`
подключается без изменений в раскладке: нужен раннер с SSH-ключом, у
которого есть доступ к `panel@77.222.55.36`. Отдельный
пользователь-деплойщик не нужен — `panel` уже в группе `docker` и не
требует `sudo` для пересборки.
