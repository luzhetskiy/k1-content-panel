import pytest

from app.ai.prompts import PROMPT_KEYS, PromptError, render_prompt, resolve_prompt
from app.models.prompt_template import PromptTemplate
from app.seed import seed_prompts


def test_seed_creates_global_defaults(db_session):
    seed_prompts(db_session)
    keys = {row.key for row in db_session.query(PromptTemplate).all()}
    assert keys == set(PROMPT_KEYS)


def test_seed_is_idempotent(db_session):
    seed_prompts(db_session)
    db_session.query(PromptTemplate).filter_by(key="topics").update({"text": "мой промпт"})
    db_session.commit()
    seed_prompts(db_session)
    row = db_session.query(PromptTemplate).filter_by(key="topics", site_id=None).one()
    assert row.text == "мой промпт"


def test_resolve_falls_back_to_global(db_session):
    seed_prompts(db_session)
    text = resolve_prompt(db_session, "topics", site_id=42)
    assert "{{ count }}" in text or "count" in text


def test_site_override_wins(db_session):
    seed_prompts(db_session)
    db_session.add(PromptTemplate(key="topics", site_id=42, text="персональный промпт"))
    db_session.commit()
    assert resolve_prompt(db_session, "topics", site_id=42) == "персональный промпт"


def test_missing_key_raises(db_session):
    with pytest.raises(PromptError, match="не найден"):
        resolve_prompt(db_session, "topics", site_id=None)


def test_render_substitutes_variables():
    result = render_prompt("Придумай {{ count }} тем для {{ site_name }}.",
                           {"count": 5, "site_name": "Стройбаза"})
    assert result == "Придумай 5 тем для Стройбаза."


def test_render_loops_over_list():
    result = render_prompt("{% for t in existing_titles %}- {{ t }}\n{% endfor %}",
                           {"existing_titles": ["А", "Б"]})
    assert result == "- А\n- Б\n"


def test_render_blocks_dangerous_attribute_access():
    """SandboxedEnvironment: промпт редактируется через админку, и обращение
    к внутренностям объектов из шаблона должно падать, а не выполняться."""
    with pytest.raises(PromptError):
        render_prompt("{{ topic.__class__.__mro__ }}", {"topic": "тема"})


def test_render_reports_syntax_error_as_text():
    with pytest.raises(PromptError, match="синтаксис"):
        render_prompt("{% for x in %}", {})


def test_render_reports_unknown_variable_by_name():
    """Опечатка в имени переменной обязана падать, а не молча подставлять
    пустоту: урезанный промпт уходит в платный запрос, и обнаружить это можно
    только по качеству статей, много позже правки шаблона."""
    with pytest.raises(PromptError, match="conut"):
        render_prompt("Придумай {{ conut }} тем.", {"count": 5})


def test_render_allows_optional_variable_via_is_defined():
    """Обратная сторона StrictUndefined: для необязательной переменной
    правильная форма — `is defined`, а не голое `{% if x %}`."""
    out = render_prompt("{% if extra is defined %}{{ extra }}{% endif %}ок", {})
    assert out == "ок"


def test_default_prompts_render_with_real_contexts(db_session):
    """Каждый дефолтный промпт прогоняется с тем набором переменных, который
    реально передаётся в бою (см. app/tasks.py и app/articles/builder.py).
    Ловит расхождение между шаблоном и вызывающим кодом — именно оно тихо
    отключало тематику сайта в промпте тем."""
    seed_prompts(db_session)
    contexts = {
        "topics": {"count": 5, "site_name": "X", "site_description": "описание",
                   "tone_of_voice": "тон", "existing_titles": ["А", "Б"]},
        "article_body": {"topic": "тема", "site_name": "X", "site_description": "описание",
                         "tone_of_voice": "тон", "reference_html": "<p>x</p>",
                         "image_count": 2, "image_paths": ["/a.webp", "/b.webp"]},
        "cover": {"topic": "тема", "cover_style": "стиль"},
        "content_image": {"topic": "тема", "paragraph": "иллюстрация 1 из 2",
                          "image_style": "стиль"},
    }
    for key in PROMPT_KEYS:
        template = resolve_prompt(db_session, key, None)
        rendered = render_prompt(template, contexts[key])
        assert rendered.strip()


def test_empty_site_override_falls_back_to_global(db_session):
    """Промпт сайта из одних пробелов — это «не задан», а не «задан пустым»."""
    seed_prompts(db_session)
    db_session.add(PromptTemplate(key="topics", site_id=1, text="   \n  "))
    db_session.commit()
    assert resolve_prompt(db_session, "topics", 1) == resolve_prompt(db_session, "topics", None)


def test_duplicate_key_for_same_site_is_rejected(db_session):
    from sqlalchemy.exc import IntegrityError

    db_session.add(PromptTemplate(key="topics", site_id=1, text="a"))
    db_session.commit()
    db_session.add(PromptTemplate(key="topics", site_id=1, text="b"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_same_key_for_different_sites_is_allowed(db_session):
    db_session.add(PromptTemplate(key="topics", site_id=1, text="a"))
    db_session.add(PromptTemplate(key="topics", site_id=2, text="b"))
    db_session.commit()
    assert db_session.query(PromptTemplate).filter_by(key="topics").count() == 2


def test_duplicate_global_prompt_is_rejected(db_session):
    """UniqueConstraint(key, site_id) глобальные шаблоны не различает: в SQL
    NULL не равен сам себе, и две строки с site_id=NULL проходят (проверено на
    живом Postgres). Их разводит отдельный частичный индекс — иначе
    resolve_prompt брал бы первую попавшуюся из дублей."""
    from sqlalchemy.exc import IntegrityError

    db_session.add(PromptTemplate(key="topics", site_id=None, text="первый"))
    db_session.commit()
    db_session.add(PromptTemplate(key="topics", site_id=None, text="второй"))
    with pytest.raises(IntegrityError):
        db_session.commit()
