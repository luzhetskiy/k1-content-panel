from app.category_meta.forms import (
    buy_queries, choose_form, declined_object, exact_query, filter_stoplist, forms_differ,
    sell_word,
)


def test_exact_query_marks_every_word():
    assert exact_query("Купить  гибкую черепицу") == '"!купить !гибкую !черепицу"'


def test_buy_queries_nominative_then_declined():
    assert buy_queries("фанера", "купить фанеру") == ('"!купить !фанера"', '"!купить !фанеру"')


def test_declined_object_strips_buy_prefix():
    assert declined_object("Купить фанеру") == "фанеру"
    assert declined_object("фанеру") == "фанеру"


def test_forms_differ():
    assert forms_differ("фанера", "купить фанеру")
    assert not forms_differ("металлический уголок", "купить металлический уголок")
    assert not forms_differ("Профнастил", "купить  профнастил")


def test_choose_form_by_frequency():
    assert choose_form(678, 234) == "nominative"
    assert choose_form(85, 120) == "declined"


def test_choose_form_falls_back_to_declined():
    assert choose_form(9, 3) == "declined"        # обе частоты ниже порога
    assert choose_form(50, 50) == "declined"      # ничья
    assert choose_form(None, None) == "declined"  # формы совпадают, проверки не было


def test_sell_word():
    assert sell_word([("фанера купить", 12238), ("фанера цена", 5300)]) == "купить"
    assert sell_word([("профнастил цена", 900), ("купить профнастил", 100)]) == "цена"
    assert sell_word([]) == "купить"


def test_filter_stoplist():
    phrases = [("фанера", 10), ("леруа фанера", 5), ("фанера Петрович", 3)]
    assert filter_stoplist(phrases, ["леруа", "петрович"]) == [("фанера", 10)]
