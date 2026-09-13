from fix_builder_logo_block_display import hide_empty_logo_text


def test_hides_empty_span_left_visible_on_reference_page():
    """13 эталонных карточек: fix_builder_reference_logo_text вставил живой
    span без hidden, и на опубликованной странице он занял полосу в 53px."""
    html = ('<div id="builder"><img id="builder-logo" src="/media/logo.svg">'
            '<span class="h2 builder-logo-text" id="builder-logo-text"></span></div>')
    fixed, changed = hide_empty_logo_text(html)
    assert changed is True
    assert 'hidden=""' in fixed
    assert "display:none" in fixed


def test_adds_inline_style_to_span_hidden_by_attribute_only():
    """56 собранных страниц: hidden проставлен, но h2,.h2{display:block} из
    CSS сайта перебивает [hidden]{display:none} браузера."""
    html = ('<div id="builder"><img id="builder-logo" src="/media/logo.svg">'
            '<span class="h2 builder-logo-text" id="builder-logo-text" hidden></span></div>')
    fixed, changed = hide_empty_logo_text(html)
    assert changed is True
    assert "display:none" in fixed


def test_leaves_span_with_text_untouched():
    """Эталон stroybaza-moscow.ru показывает и логотип, и подпись «ПИК» —
    это ручная вёрстка живой страницы, а не наш запасной вариант."""
    html = ('<div id="builder"><img id="builder-logo" src="/media/logo.svg">'
            '<span class="h2 builder-logo-text" id="builder-logo-text">ПИК</span></div>')
    fixed, changed = hide_empty_logo_text(html)
    assert changed is False
    assert fixed == html


def test_adds_inline_style_to_hidden_image():
    html = ('<div id="builder"><img id="builder-logo" hidden>'
            '<span class="h2 builder-logo-text" id="builder-logo-text">ООО Дом</span></div>')
    fixed, changed = hide_empty_logo_text(html)
    assert changed is True
    assert "display:none" in fixed


def test_keeps_unrelated_inline_styles():
    html = ('<div id="builder"><img id="builder-logo" src="/media/logo.svg">'
            '<span id="builder-logo-text" hidden style="color:red"></span></div>')
    fixed, _ = hide_empty_logo_text(html)
    assert "color:red" in fixed
    assert "display:none" in fixed


def test_reports_no_change_when_already_fixed():
    html = ('<div id="builder"><img id="builder-logo" src="/media/logo.svg">'
            '<span id="builder-logo-text" hidden style="display:none"></span></div>')
    fixed, changed = hide_empty_logo_text(html)
    assert changed is False
    assert fixed == html


def test_page_without_logo_block_untouched():
    html = '<div id="builder"><h2 id="builder-main-title">О компании</h2></div>'
    fixed, changed = hide_empty_logo_text(html)
    assert changed is False
    assert fixed == html
