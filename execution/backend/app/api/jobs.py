"""Журнал задач. `cost` — сумма LlmUsage.cost (Task 7/14), а это, в свою
очередь, usage.cost из ответа RouterAI — нестандартное расширение, которого
может не быть у конкретной модели. Ноль в этом поле не обязательно значит
«бесплатно»: если провайдер его не прислал, TextClient пишет предупреждение
в лог (app/ai/text.py, _usage) и подставляет 0. Отличить «правда бесплатно»
от «не сообщили» по одной только цифре в журнале нельзя — при подозрении
смотреть логи воркера за нужный job_run_id."""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.job import JobRun
from app.models.site import Site
from app.models.user import User

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobOut(BaseModel):
    id: int
    kind: str
    site_name: str
    status: str
    log_text: str
    cost: float
    tokens_total: int
    started_at: datetime
    finished_at: datetime | None


@router.get("", response_model=list[JobOut])
def list_jobs(limit: int = 100, offset: int = 0, db: Session = Depends(get_db),
              _user: User = Depends(get_current_user)):
    jobs = db.scalars(
        select(JobRun).order_by(JobRun.id.desc()).limit(limit).offset(offset)).all()
    result = []
    for job in jobs:
        site = db.get(Site, job.site_id) if job.site_id else None
        result.append(JobOut(
            id=job.id, kind=job.kind, site_name=site.name if site else "—",
            status=job.status, log_text=job.log_text,
            # Находка №3 ревью Task 18: LlmUsage.cost — float (docstring
            # app/models/job.py прямо предупреждает об этом заранее и требует
            # округления именно здесь). Сумма нескольких float даёт
            # накопленную ошибку двоичного представления — проверено
            # эмпирически (см. test_jobs_cost_is_rounded_for_display в
            # test_api_batches.py): sum([0.1, 0.2]) == 0.30000000000000004,
            # не 0.3. Без round() такой хвост ушёл бы прямо в ответ API.
            # round(x, 2) — это округление для отображения, а не изменение
            # того, что хранится в БД (там остаётся как пришло от провайдера,
            # см. docstring app/models/job.py).
            cost=round(sum(u.cost for u in job.usage), 2),
            tokens_total=sum(u.tokens_prompt + u.tokens_completion for u in job.usage),
            started_at=job.started_at, finished_at=job.finished_at,
        ))
    return result
