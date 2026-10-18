from app.house_projects.validate import (
    list_sizes, numbers, shared_shingles, validate_rewrite, volume_range,
)

ORIGINAL = ("<p>Представляем проект уютного дома площадью 82.64 м², идеально подходящего "
            "для семьи из четырёх человек.</p><br>"
            "<h3>Ключевые преимущества <br>и удобства:</h3>"
            "<ul><li>Монолитная плита под всем домом.</li>"
            "<li>Терраса 9.06 м² для отдыха.</li></ul>")
REWRITE = ("<p>Перед вами комфортный дом на 82,64 м², который отлично подойдёт "
           "семье, где растут двое детей.</p><br>"
           "<h3>Почему выбирают <br>этот проект:</h3>"
           "<ul><li>Под зданием залита монолитная плита.</li>"
           "<li>Для отдыха есть терраса 9.06 м².</li></ul>")


def test_good_rewrite_passes():
    assert validate_rewrite(ORIGINAL, REWRITE) == []


def test_numbers_normalize_comma_and_split_dimensions():
    assert numbers("<p>82,64 м² и 11×9 м</p>") == {"82.64", "11", "9"}
    assert numbers("<p>82.64</p>") == numbers("<p>82,64</p>")


def test_missing_number_is_reported():
    errors = validate_rewrite(ORIGINAL, REWRITE.replace("9.06", "девять"))
    assert any("пропали числа из оригинала: 9.06" in e for e in errors)


def test_missing_number_keeps_original_spelling():
    original = ORIGINAL.replace("82.64", "82,64")
    errors = validate_rewrite(original, REWRITE.replace("82,64 м²", "восемьдесят с лишним м²"))
    assert any("пропали числа из оригинала: 82,64 —" in e for e in errors)


def test_invented_number_is_reported_but_single_digit_is_not():
    invented = REWRITE.replace("монолитная плита.", "монолитная плита на 100 лет.")
    assert any("числа, которых нет в оригинале: 100" in e
               for e in validate_rewrite(ORIGINAL, invented))
    retold = REWRITE.replace("растут двое детей", "растут 2 детей")
    assert validate_rewrite(ORIGINAL, retold) == []


def test_volume_bounds():
    low, high = volume_range("<p>" + "а" * 100 + "</p>")
    assert (low, high) == (90, 110)
    original = "<p>" + "слово " * 50 + "</p>"
    assert any("объём" in e for e in validate_rewrite(original, "<p>коротко</p>"))


def test_structure_changes_are_reported():
    no_heading = REWRITE.replace("<h3>Почему выбирают <br>этот проект:</h3>",
                                 "<p>Почему выбирают этот проект:</p>")
    assert any("подзаголовков <h3> 0, в оригинале 1" in e
               for e in validate_rewrite(ORIGINAL, no_heading))
    merged = REWRITE.replace("</li><li>", " ")
    assert any("пунктов в списках [1], в оригинале [2]" in e
               for e in validate_rewrite(ORIGINAL, merged))
    assert list_sizes("<p>без списков</p>") == []


def test_text_around_html_is_reported():
    errors = validate_rewrite(ORIGINAL, "Вот вариант:\n" + REWRITE)
    assert any("не чистый HTML" in e for e in errors)
    assert not any("не чистый HTML" in e for e in validate_rewrite(ORIGINAL, " " + REWRITE + "\n"))


def test_foreign_tag_is_reported():
    errors = validate_rewrite(ORIGINAL, REWRITE.replace("<p>Перед", '<p><a href="/">Перед</a>'))
    assert any("теги, которых нет в оригинале: a" in e for e in errors)


def test_copy_is_too_close():
    assert shared_shingles(ORIGINAL, ORIGINAL) == 1.0
    errors = validate_rewrite(ORIGINAL, ORIGINAL)
    assert any("слишком близко к оригиналу: совпадает 100 %" in e for e in errors)
    assert shared_shingles("<p>два слова</p>", "<p>два слова</p>") == 0.0
