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
