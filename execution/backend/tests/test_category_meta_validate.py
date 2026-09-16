import pytest

from app.category_meta.validate import (
    MetaContext, allowed_keywords, normalize_tags, split_phrases, validate_tags,
)

PHRASES = ["фанера", "фанера купить", "фанера цена", "фанера москва",
           "купить фанеру в москве", "лист фанеры цена", "фанера влагостойкая",
           "ламинированная фанера", "фанера фсф", "фанера фк"]


def ctx(**overrides):
    values = dict(form_nominative="фанера", form_buy="купить фанеру",
                  chosen_form="nominative", city="Москва", city_in="в Москве",
                  brand="Стройбаза", wordstat_phrases=PHRASES,
                  stoplist=["леруа", "петрович"],
                  site_description="Интернет-магазин стройматериалов, доставка по Москве")
    values.update(overrides)
    return MetaContext(**values)


def valid_tags(**overrides):
    tags = {
        "title": "Фанера в Москве — купить по выгодной цене | Стройбаза",
        "h1": "Фанера в Москве",
        "meta_description": ("Фанера в Москве по выгодной цене: влагостойкая ФСФ, ФК, "
                             "ламинированная и берёзовая. Листы 1525×1525 и 2440×1220 мм, "
                             "толщина 4–40 мм. Доставка по Москве."),
        "meta_keywords": ("фанера, фанера купить, фанера цена, фанера москва, "
                          "купить фанеру в москве, лист фанеры цена, фанера влагостойкая, "
                          "ламинированная фанера, фанера фсф, фанера фк"),
        "ai_keywords": ("где купить фанеру в Москве, какая фанера подходит для пола, "
                        "влагостойкая фанера ФСФ цена, фанера ФК или ФСФ что выбрать, "
                        "ламинированная фанера для опалубки, фанера 18 мм купить в Москве, "
                        "лист фанеры 1525×1525 цена, берёзовая фанера для мебели, "
                        "фанера с доставкой по Москве, сколько стоит лист фанеры"),
    }
    tags.update(overrides)
    return tags


def test_valid_tags_pass():
    assert validate_tags(valid_tags(), ctx()) == []


def test_declined_form_requires_title_to_start_with_buy_form():
    tags = valid_tags(title="Купить фанеру в Москве по выгодной цене | Стройбаза")
    assert validate_tags(tags, ctx(chosen_form="declined")) == []
    errors = validate_tags(valid_tags(), ctx(chosen_form="declined"))
    assert "title должен начинаться с «купить фанеру»" in errors


def test_title_rules():
    long_title = "Фанера в Москве — купить по выгодной цене, большой выбор листов | Стройбаза"
    errors = validate_tags(valid_tags(title=long_title), ctx())
    assert any(e.startswith("title длиннее 70") for e in errors)
    errors = validate_tags(valid_tags(title="Фанера в Москве — купить по выгодной цене"), ctx())
    assert "title должен оканчиваться на « | Стройбаза»" in errors
    errors = validate_tags(valid_tags(title="Фанера — купить по выгодной цене | Стройбаза"), ctx())
    assert "в title нет «в Москве»" in errors
    errors = validate_tags(valid_tags(title="Фанера в Москве — большой выбор | Стройбаза"), ctx())
    assert "в title нет продающего слова «купить» или «цена»" in errors


def test_h1_rules():
    assert "в h1 не должно быть бренда" in validate_tags(
        valid_tags(h1="Фанера в Москве — Стройбаза"), ctx())
    assert "в h1 нет «в Москве»" in validate_tags(valid_tags(h1="Фанера"), ctx())
    assert "h1 не должен повторять title" in validate_tags(
        valid_tags(h1="Фанера в Москве — купить по выгодной цене"), ctx())
    assert "h1 пустой" in validate_tags(valid_tags(h1=""), ctx())


def test_description_length_and_city():
    errors = validate_tags(valid_tags(meta_description="Фанера в Москве."), ctx())
    assert any(e.startswith("description должен быть 140–170") for e in errors)
    no_city = valid_tags()["meta_description"].replace("в Москве", "в городе")
    assert "в description нет «в Москве»" in validate_tags(valid_tags(meta_description=no_city), ctx())


def test_description_forbidden_claims_unless_site_says_so():
    text = ("Фанера в Москве в наличии на складе: влагостойкая ФСФ, ФК, ламинированная и "
            "берёзовая. Листы 1525×1525 и 2440×1220 мм, толщина 4–40 мм, доставка.")
    tags = valid_tags(meta_description=text)
    assert "в description утверждение «в наличии», которого нет в описании сайта" in \
        validate_tags(tags, ctx())
    assert validate_tags(tags, ctx(site_description="Всё в наличии на складе в Москве")) == []


def test_opt_matches_whole_word_only():
    text = ("Фанера в Москве — оптимальный выбор для пола и стен: влагостойкая ФСФ, ФК, "
            "ламинированная. Листы 1525×1525 и 2440×1220 мм, толщина 4–40 мм.")
    assert not any("оптом" in e for e in validate_tags(valid_tags(meta_description=text), ctx()))


def test_keywords_limit_and_source():
    eleven = valid_tags()["meta_keywords"] + ", фанера москва"
    assert "keywords: больше 10 фраз (11)" in validate_tags(valid_tags(meta_keywords=eleven), ctx())
    errors = validate_tags(valid_tags(meta_keywords="фанера, фанера оптом дешево"), ctx())
    assert "keywords не из статистики Wordstat: фанера оптом дешево" in errors
    assert "keywords пустые" in validate_tags(valid_tags(meta_keywords=""), ctx())


def test_city_variants_are_allowed_keywords():
    allowed = allowed_keywords(ctx(wordstat_phrases=[]))
    assert {"фанера москва", "фанера в москве", "купить фанеру в москве"} <= allowed


def test_ai_keywords_count():
    errors = validate_tags(valid_tags(ai_keywords="где купить фанеру"), ctx())
    assert "ai_keywords: нужно 10–15 фраз (1)" in errors


def test_stoplist_in_any_field():
    tags = valid_tags(ai_keywords=valid_tags()["ai_keywords"] + ", фанера в леруа")
    assert "в ai_keywords слова из стоп-листа: леруа" in validate_tags(tags, ctx())


def test_normalize_tags_joins_lists_and_lowercases_keywords():
    tags = normalize_tags({"title": "  Фанера   в Москве ", "h1": "Фанера",
                           "meta_description": "x", "meta_keywords": ["Фанера", "Фанера Цена"],
                           "ai_keywords": "Где купить,  фанеру ,"})
    assert tags["title"] == "Фанера в Москве"
    assert tags["meta_keywords"] == "фанера, фанера цена"
    assert tags["ai_keywords"] == "Где купить, фанеру"


def test_normalize_tags_rejects_non_object():
    with pytest.raises(ValueError):
        normalize_tags(["title"])


def test_split_phrases_drops_empty():
    assert split_phrases("а, , б ,") == ["а", "б"]
