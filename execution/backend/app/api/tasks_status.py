"""Опрос состояния фоновой задачи Celery. Не привязан к конкретной задаче:
любая, чей task_id известен фронту, опрашивается одинаково."""

from celery.result import AsyncResult
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.celery_app import celery_app
from app.models.user import User

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskStatus(BaseModel):
    state: str
    result: object | None = None


@router.get("/{task_id}/status", response_model=TaskStatus)
def task_status(task_id: str, _user: User = Depends(get_current_user)):
    result = AsyncResult(task_id, app=celery_app)
    return TaskStatus(state=result.state, result=result.result if result.ready() else None)
