from app.ai.prompts import render_prompt, resolve_prompt
from app.seed import seed_prompts

META_VARS = {"site_name": "Стройбаза", "site_description": "Магазин, доставка по Москве",
             "category_name": "Фанера", "category_path": "Листовые материалы / Фанера",
             "form_nominative": "фанера", "form_buy": "купить фанеру", "form_price": "цена фанеры",
             "chosen_form": "nominative", "sell_word": "купить", "city_in": "в Москве",
             "brand": "Стройбаза", "total_count": 96275,
             "phrases": ["фанера — 96275", "фанера купить — 12238"], "alternatives": [],
             "violations": []}


def render(db, **overrides):
    seed_prompts(db)
    return render_prompt(resolve_prompt(db, "category_meta", None), {**META_VARS, **overrides})


def test_meta_prompt_nominative_instruction(db_session):
    text = render(db_session)
    assert "начни title с «фанера»" in text
    assert "начни title с «купить фанеру»" not in text
    assert "фанера купить — 12238" in text
    assert "не прошёл проверку" not in text


def test_meta_prompt_lists_alternatives(db_session):
    assert "Другие названия" not in render(db_session)
    text = render(db_session, alternatives=["гкл — 24437: гкл, потолок гкл"])
    assert "Другие названия этих товаров" in text
    assert "гкл — 24437: гкл, потолок гкл" in text


def test_meta_prompt_declined_instruction(db_session):
    text = render(db_session, chosen_form="declined")
    assert "начни title с «купить фанеру»" in text


def test_meta_prompt_lists_violations(db_session):
    text = render(db_session, violations=["h1 пустой", "в title нет «в Москве»"])
    assert "Предыдущий вариант не прошёл проверку" in text
    assert "- в title нет «в Москве»" in text


def test_seeds_prompt_lists_categories(db_session):
    seed_prompts(db_session)
    text = render_prompt(resolve_prompt(db_session, "category_seeds", None),
                         {"site_name": "С", "site_description": "о",
                          "categories": ["46: Листовые материалы / Фанера", "62: Крепеж / Анкер"]})
    assert "46: Листовые материалы / Фанера\n62: Крепеж / Анкер" in text
