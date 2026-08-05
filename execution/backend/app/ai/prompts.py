"""Разрешение промпта (сайт → глобальный дефолт) и безопасный рендер Jinja2."""

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.prompt_template import PromptTemplate

PROMPT_KEYS = ("topics", "article_body", "cover", "content_image")

_env = SandboxedEnvironment(autoescape=False, trim_blocks=False, lstrip_blocks=False)


class PromptError(RuntimeError):
    pass


def resolve_prompt(db: Session, key: str, site_id: int | None) -> str:
    """Сначала переопределение сайта, затем глобальный дефолт."""
    if site_id is not None:
        override = db.scalars(
            select(PromptTemplate).where(PromptTemplate.key == key,
                                         PromptTemplate.site_id == site_id)).first()
        if override and override.text.strip():
            return override.text

    default = db.scalars(
        select(PromptTemplate).where(PromptTemplate.key == key,
                                     PromptTemplate.site_id.is_(None))).first()
    if default is None:
        raise PromptError(f"промпт {key!r} не найден — выполни seed_prompts")
    return default.text


def render_prompt(template_text: str, variables: dict) -> str:
    try:
        return _env.from_string(template_text).render(**variables)
    except TemplateError as exc:
        # Промпты редактируются через админку, поэтому ошибка шаблона — обычная
        # пользовательская ошибка, а не сбой сервиса: её надо показать текстом.
        raise PromptError(f"ошибка шаблона (синтаксис или доступ): {exc}") from exc
