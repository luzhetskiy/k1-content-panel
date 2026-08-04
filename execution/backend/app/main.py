from fastapi import FastAPI

from app.api import auth

app = FastAPI(title="k1 content service")

app.include_router(auth.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
