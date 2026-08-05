"""Разрешение промпта (сайт → глобальный дефолт) и безопасный рендер Jinja2."""

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.prompt_template import PromptTemplate

PROMPT_KEYS = ("topics", "article_body", "cover", "content_image")

# undefined=StrictUndefined: с дефолтным Undefined опечатка в имени переменной
# ({{ conut }} вместо {{ count }}) молча превращается в пустую строку — шаблон
# рендерится «успешно», часть инструкции исчезает, и урезанный промпт уходит в
# платный запрос к модели. Обнаружить это можно только по качеству статей,
# то есть сильно позже и без связи с правкой промпта. Так уже было: шаблон
# topics обращался к site_description и tone_of_voice, а вызывающий код их не
# передавал — тематика сайта тихо не доезжала до модели.
#
# Обратная сторона: при StrictUndefined условие {% if x %} для необязательной
# переменной тоже падает. Правильная форма — {% if x is defined %}.
#
# В контекст рендера передаются только плоские значения (строки, числа, списки
# строк). ORM-объекты передавать нельзя: песочница ограничивает доступ к
# «небезопасным» атрибутам, но обычные атрибуты объекта из шаблона доступны.
_env = SandboxedEnvironment(autoescape=False, trim_blocks=False, lstrip_blocks=False,
                            undefined=StrictUndefined)


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
