from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.clock import utcnow
from app.db import Base

# JSONB на Postgres, обычный JSON на SQLite в тестах — один и тот же код моделей
# работает в обоих контурах.
JsonType = JSON().with_variant(JSONB(), "postgresql")


class JobRun(Base):
    """Журнал фоновых задач: кто, что, когда и чем кончилось.

    status по умолчанию "running", а не "pending" — единственная модель в
    проекте с таким дефолтом. Это осознанно: JobRun создаётся уже внутри
    Celery-задачи, которая начала выполняться (см. _start_job в Task 17,
    app/tasks.py — вызывается из generate_topics_sync/run_batch_sync/
    retry_article_sync, то есть из тела уже запущенной задачи, а не перед
    постановкой в очередь). Если брокер недоступен, `.delay()`/`apply_async()`
    в API (Task 18) бросит исключение ДО того, как строка JobRun вообще
    появится, — то есть зависшего "running", который никогда не стартовал,
    таким путём не возникает.
    Остаточный риск — не «никогда не стартовавшая» запись, а «стартовавшая и
    не досчитавшая до конца»: воркер убит по OOM или SIGKILL, потеряно
    соединение с БД внутри `except` до commit — в этих случаях JobRun
    останется в "running" навсегда, потому что `_finish_job` не будет вызван.
    Обработчик SoftTimeLimitExceeded в tasks.py закрывает мягкий случай
    (истечение времени), но не жёсткий сбой процесса. Схема уже даёт всё
    нужное для обнаружения зависших записей без изменений: `started_at`
    есть у каждой JobRun, и запрос вида `status='running' AND started_at <
    now() - interval` находит их без дополнительного поля. Это не чинится
    в Task 14 — отмечено как риск для будущего экрана журнала задач
    (Task 18/23): такой запрос там нужно предусмотреть, а не изобретать поле.
    """

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))       # generate_topics | build_article
    # SET NULL: журнал расходов и логов должен пережить удаление сайта —
    # это операционная история/costs, ценность которой не привязана к тому,
    # заведён ли ещё сам сайт в панели. См. тот же выбор и то же обоснование
    # у ArticleBatch.site_id и Article.site_id (app/models/article.py).
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sites.id", ondelete="SET NULL"), nullable=True)
    params_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    celery_task_id: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|ok|failed
    log_text: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    usage: Mapped[list["LlmUsage"]] = relationship(
        back_populates="job", cascade="all, delete-orphan")


class LlmUsage(Base):
    """Расход RouterAI. Картинка в качестве high стоит ≈16.8 единицы —
    расход надо видеть до того, как он станет сюрпризом.

    cost — float, не Decimal: источник данных сам float (TextResult.cost,
    ImageResult.cost в app/ai/text.py и app/ai/images.py приходят из ответа
    RouterAI как float), так что Decimal здесь дал бы ложную точность без
    исправления источника. Но Task 18 суммирует cost по всем LlmUsage джобы
    (`sum(u.cost for u in job.usage)`) — накопленная ошибка двоичного float
    на сумме из нескольких чисел может дать в ответе API что-то вроде
    5.399999999999999 вместо 5.4. Округление обязано делаться на стороне
    отображения (round(x, 2) в Task 18/25 при формировании ответа), а не
    здесь: хранить нужно то, что реально пришло от провайдера.
    """

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    # index=True: Task 18 (app/api/jobs.py) считает cost и tokens_total через
    # `job.usage` для каждой строки списка джобов на /api/jobs — то есть этот
    # фильтр по job_run_id выполняется на каждый показанный ряд журнала,
    # а не один раз на всю страницу.
    job_run_id: Mapped[int] = mapped_column(
        ForeignKey("job_runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))       # text | image
    model: Mapped[str] = mapped_column(String(100), default="")
    tokens_prompt: Mapped[int] = mapped_column(Integer, default=0)
    tokens_completion: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped["JobRun"] = relationship(back_populates="usage")
