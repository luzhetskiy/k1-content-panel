from app.articles.topics import filter_duplicates, normalize


def test_normalize_lowercases_and_drops_punctuation():
    # Без "Как" в начале: это слово — стоп-слово (см. _STOPWORDS), его отсев
    # проверяется отдельно в test_normalize_drops_stopwords. Здесь проверяем
    # только регистр и пунктуацию, а не пересечение с фильтром стоп-слов.
    assert normalize("Выбрать Фундамент!") == "vybrat fundament"


def test_normalize_drops_stopwords():
    assert normalize("Как и чем утеплить дом") == "uteplit dom"


def test_exact_duplicate_is_filtered():
    kept, dropped = filter_duplicates(["Чем утеплить дом"], ["Чем утеплить дом"])
    assert kept == []
    assert dropped == ["Чем утеплить дом"]


def test_case_and_punctuation_insensitive():
    kept, _ = filter_duplicates(["ЧЕМ УТЕПЛИТЬ ДОМ?"], ["Чем утеплить дом"])
    assert kept == []


def test_near_duplicate_by_keyword_overlap_is_filtered():
    """«Чем утеплить каркасный дом» и «Как утеплить каркасный дом зимой» —
    одна и та же статья с точки зрения читателя."""
    kept, _ = filter_duplicates(["Как утеплить каркасный дом зимой"],
                                ["Чем утеплить каркасный дом"])
    assert kept == []


def test_different_topic_is_kept():
    kept, dropped = filter_duplicates(["Как выбрать кровельное покрытие"],
                                      ["Чем утеплить каркасный дом"])
    assert kept == ["Как выбрать кровельное покрытие"]
    assert dropped == []


def test_duplicates_inside_proposed_list_are_filtered():
    kept, _ = filter_duplicates(["Чем утеплить дом", "Чем утеплить дом фасад"], [])
    assert len(kept) == 1


def test_empty_existing_keeps_everything():
    # Не "Тема A"/"Тема B": "A" транслитерируется в "a", а это же "a" — форма
    # русского союза "а" в _STOPWORDS. normalize("Тема A") даёт токены {tema}
    # (без "a"), normalize("Тема B") — {tema, b}; overlap 1/1 = 1.0 >= порога,
    # и функция (ошибочно для намерения этого теста) считает их дублями.
    # "Раз"/"Два" не пересекаются со стоп-словами и с собой.
    kept, dropped = filter_duplicates(["Тема Раз", "Тема Два"], [])
    assert len(kept) == 2
    assert dropped == []


def test_overlap_exactly_at_threshold_is_filtered():
    """OVERLAP_THRESHOLD = 0.6 — граница включительная (>=, не >). Пять
    значимых слов, три общих: overlap = 3 / 5 = 0.6 ровно."""
    kept, dropped = filter_duplicates(
        ["alpha bravo charlie delta echo"], ["alpha bravo charlie foxtrot golf"])
    assert kept == []
    assert dropped == ["alpha bravo charlie delta echo"]


def test_topic_made_only_of_stopwords_is_dropped_not_kept():
    """Мусорный ответ модели (одни стоп-слова/пунктуация) нормализуется в
    пустой набор токенов и намеренно уходит в dropped вместе с настоящими
    дублями — отдельной категории "невалидная тема" нет (см. комментарий у
    _is_duplicate). Тема без ключевых слов не должна попасть в kept."""
    kept, dropped = filter_duplicates(["Как и почему", "???"], [])
    assert kept == []
    assert dropped == ["Как и почему", "???"]
