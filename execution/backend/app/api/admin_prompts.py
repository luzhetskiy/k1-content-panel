"""Экран «Промпты»: глобальные шаблоны, переопределения по сайту и прогон
шаблона на живой модели без сохранения результата."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.factory import AIConfigError, build_text_client
from app.ai.prompts import PROMPT_KEYS, PromptError, check_template, render_prompt
from app.ai.text import LLMError
from app.api.deps import get_db, require_role
from app.models.prompt_template import PromptTemplate
from app.models.site import Site
from app.models.user import User
from app.seed import seed_prompts

router = APIRouter(prefix="/api/admin/prompts", tags=["admin-prompts"])

# Одна попытка вместо llm_max_retries: три попытки — это до 366 с ожидания
# (120 с таймаута × 3 + паузы backoff, см. app/ai/text.py) в синхронном
# HTTP-запросе, который всё это время держит и поток, и сессию БД. Ретраи
# нужны фоновым задачам, где повтор некому нажать; здесь админ сидит перед
# экраном и нажмёт «Тест» сам.
TEST_MAX_RETRIES = 1


class PromptOut(BaseModel):
    id: int
    key: str
    site_id: int | None
    text: str


class PromptIn(BaseModel):
    key: str
    site_id: int | None = None
    text: str


class PromptTestIn(BaseModel):
    text: str
    # default_factory, а не {}: pydantic v2 копирует изменяемый дефолт на
    # каждый экземпляр (проверено на pydantic 2.10.4), но полагаться на это
    # поведение библиотеки в общем на запрос объекте не хочется.
    variables: dict = Field(default_factory=dict)


class PromptTestOut(BaseModel):
    rendered: str
    answer: str
    tokens_total: int
    cost: float


def _find(db: Session, key: str, site_id: int | None) -> PromptTemplate | None:
    # `== None` тут не ошибка и не требует ветки с .is_(None): SQLAlchemy
    # рендерит сравнение с None как `IS NULL` (проверено печатью запроса на
    # sqlalchemy 2.x), поэтому один и тот же вызов находит и глобальный
    # шаблон, и переопределение сайта.
    return db.scalars(
        select(PromptTemplate).where(PromptTemplate.key == key,
                                     PromptTemplate.site_id == site_id)).first()


@router.get("", response_model=list[PromptOut])
def list_prompts(db: Session = Depends(get_db),
                 _user: User = Depends(require_role("admin", "manager"))):
    seed_prompts(db)
    rows = db.scalars(select(PromptTemplate).order_by(PromptTemplate.key,
                                                      PromptTemplate.site_id)).all()
    return [PromptOut(id=r.id, key=r.key, site_id=r.site_id, text=r.text) for r in rows]


@router.put("", response_model=PromptOut)
def save_prompt(payload: PromptIn, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin", "manager"))):
    """Проверки здесь, а не «потом разберёмся»: у сломанного промпта следующая
    точка обнаружения — Celery-задача генерации статьи, где ошибку уже никто
    не свяжет с правкой шаблона. Экран «Тест» тут не защита: сохранить можно
    и не нажав его."""
    if payload.key not in PROMPT_KEYS:
        raise HTTPException(400, f"неизвестный ключ промпта: {payload.key}")
    if payload.site_id is not None and db.get(Site, payload.site_id) is None:
        # Иначе строка с висячим site_id либо ложится в БД (SQLite, внешние
        # ключи выключены), либо валит commit необработанным IntegrityError.
        raise HTTPException(404, "сайт не найден")
    if payload.site_id is None and not payload.text.strip():
        # Для переопределения сайта пустой текст осмыслен — resolve_prompt
        # возвращается к глобальному шаблону. Для самого глобального пустой
        # текст означает пустой платный запрос в модель.
        raise HTTPException(400, "глобальный промпт не может быть пустым")
    try:
        check_template(payload.text, payload.key)
    except PromptError as exc:
        raise HTTPException(400, str(exc)) from exc

    row = _find(db, payload.key, payload.site_id)
    if row is None:
        row = PromptTemplate(key=payload.key, site_id=payload.site_id)
        db.add(row)
    row.text = payload.text
    try:
        db.commit()
    except IntegrityError:
        # Конкурентная первая запись того же ключа: между нашим SELECT
        # (промах) и INSERT строку успел вставить другой админ — тот же класс
        # гонки, что чинили в Task 5 для SettingsService._upsert. К моменту
        # повтора строка уже есть, поэтому оставшийся путь — UPDATE. Если её
        # всё же нет, причина конфликта другая, и прятать её нельзя.
        db.rollback()
        row = _find(db, payload.key, payload.site_id)
        if row is None:
            raise
        row.text = payload.text
        db.commit()
    return PromptOut(id=row.id, key=row.key, site_id=row.site_id, text=row.text)


@router.post("/test", response_model=PromptTestOut)
def test_prompt(payload: PromptTestIn, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin", "manager"))):
    """Прогон шаблона без сохранения результата: видно и отрендеренный промпт,
    и ответ модели, и цену вопроса."""
    try:
        rendered = render_prompt(payload.text, payload.variables)
    except PromptError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not rendered.strip():
        # Пустой промпт — платный запрос ни о чём с заведомо мусорным ответом.
        raise HTTPException(400, "шаблон отрендерился в пустую строку")

    try:
        client = build_text_client(db, max_retries=TEST_MAX_RETRIES)
    except AIConfigError as exc:
        # Ошибка настроек панели, а не отказ провайдера: 400, чтобы админ
        # чинил её у себя, а не искал проблему на стороне RouterAI.
        raise HTTPException(400, str(exc)) from exc
    try:
        result = client.complete_text(rendered)
    except LLMError as exc:
        raise HTTPException(502, f"RouterAI: {exc}") from exc

    return PromptTestOut(
        rendered=rendered, answer=result.text,
        tokens_total=result.tokens_prompt + result.tokens_completion,
        cost=result.cost,
    )
