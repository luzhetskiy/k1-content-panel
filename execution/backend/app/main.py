"""Сборка FastAPI-приложения.

Два намеренных решения, зафиксированных здесь, чтобы их не «починили» при
будущей правке:

1. CORS отсутствует осознанно, не по недосмотру. И в разработке (Vite
   проксирует `/api` на бэкенд), и в проде (nginx, Task 26) фронт и API
   обслуживаются с одного origin — добавление `CORSMiddleware` было бы
   регрессом, открывающим API для чтения с чужих origin.
2. `/docs`, `/redoc`, `/openapi.json` включены по умолчанию FastAPI и не
   отключены. Сегодня это безопасно: nginx (Task 26) проксирует наружу
   только `location /api/`, а корневой `/` отдаёт статику SPA — сам FastAPI
   снаружи недостижим. Если этот блок nginx когда-нибудь расширят до
   `location /`, вся схема API станет публично перечислимой через `/docs`.
"""

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api import admin_settings, admin_sites, auth, sites
from app.api.deps import get_db

app = FastAPI(title="k1 content service")

app.include_router(auth.router)
app.include_router(admin_settings.router)
app.include_router(sites.router)
app.include_router(admin_sites.router)


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    # Дымовая проверка после выкладки (DEPLOY.md, Task 26) полагается на этот
    # эндпоинт как на единственный сигнал "сервис работает". Статический
    # {"status": "ok"} отвечал бы так же и при лежащем Postgres — реальный
    # запрос к БД превращает проверку из "жив ли uvicorn" в "работает ли
    # сервис".
    db.execute(text("select 1"))
    return {"status": "ok"}
