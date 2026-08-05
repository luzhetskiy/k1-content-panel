"""Разрешение промпта (сайт → глобальный дефолт) и безопасный рендер Jinja2."""

from jinja2 import StrictUndefined, TemplateError, meta
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.prompt_template import PromptTemplate

PROMPT_KEYS = ("topics", "article_body", "cover", "content_image")

# Набор переменных, который каждому промпту реально передаёт боевой код
# (app/tasks.py и app/articles/builder.py). Объявлен здесь, а не разбросан по
# местам вызова, ради двух проверок сразу: check_template отклоняет опечатку в
# имени переменной при сохранении шаблона в админке, а
# test_default_prompts_render_with_real_contexts сверяет этот список с
# контекстами, которые собирает вызывающий код. Без первой проверки опечатка
# вида {{ site_desription }} сохраняется с ответом 200 (синтаксис-то верный) и
# всплывает только на рендере в Celery-задаче — то есть посреди партии, после
# того как за предыдущие статьи уже заплачено.
PROMPT_VARIABLES: dict[str, frozenset[str]] = {
    "topics": frozenset({"count", "site_name", "site_description", "tone_of_voice",
                         "existing_titles"}),
    "article_body": frozenset({"topic", "site_name", "site_description", "tone_of_voice",
                               "reference_html", "image_count", "image_paths"}),
    "cover": frozenset({"topic", "cover_style"}),
    "content_image": frozenset({"topic", "paragraph", "image_style"}),
}

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


def check_template(template_text: str, key: str | None = None) -> None:
    """Проверка шаблона без рендера — для сохранения в админке (Task 13), где
    значения переменных ещё неизвестны. Без неё сломанный шаблон ложится в БД
    с ответом 200 и взрывается позже, внутри Celery-задачи генерации статьи,
    где ошибку уже никто не свяжет с правкой промпта.

    При известном `key` проверяются и имена переменных: `{{ site_desription }}`
    синтаксически безупречен, но на рендере с StrictUndefined это отказ. Ждать
    его до боевого прогона незачем — набор переменных для каждого ключа
    известен заранее (PROMPT_VARIABLES), так что опечатка называется по имени
    прямо в форме редактирования.

    Переменные цикла (`{% for title in existing_titles %}`) в список
    неизвестных не попадают: find_undeclared_variables считает объявленным всё,
    что шаблон присваивает сам."""
    try:
        ast = _env.parse(template_text)
    except TemplateError as exc:
        raise PromptError(f"ошибка шаблона (синтаксис): {exc}") from exc

    allowed = PROMPT_VARIABLES.get(key or "")
    if allowed is None:
        return
    unknown = sorted(meta.find_undeclared_variables(ast) - allowed)
    if unknown:
        raise PromptError(
            f"неизвестные переменные: {', '.join(unknown)}. "
            f"Для промпта {key!r} доступны: {', '.join(sorted(allowed))}")


def render_prompt(template_text: str, variables: dict) -> str:
    try:
        return _env.from_string(template_text).render(**variables)
    except TemplateError as exc:
        # Промпты редактируются через админку, поэтому ошибка шаблона — обычная
        # пользовательская ошибка, а не сбой сервиса: её надо показать текстом.
        raise PromptError(f"ошибка шаблона (синтаксис или доступ): {exc}") from exc
