from fix_builder_reference_logo_text import ensure_logo_text


def test_uncomments_existing_logo_text_span():
    """9 сайтов из 13: span есть, но закомментирован."""
    html = """
    <div id="builder">
      <img id="builder-logo" src="/media/logo-pik.svg" alt="ПИК">
      <!--
        <span class="h2 builder-logo-text" id="builder-logo-text">ПИК</span>
      -->
    </div>
    """
    fixed, changed = ensure_logo_text(html)
    assert changed is True
    assert '<!--' not in fixed
    assert 'id="builder-logo-text"' in fixed


def test_inserts_logo_text_span_when_absent():
    """4 сайта из 13: элемента нет вовсе."""
    html = '<div id="builder"><img id="builder-logo" src="/media/logo.svg"></div>'
    fixed, changed = ensure_logo_text(html)
    assert changed is True
    assert 'class="h2 builder-logo-text"' in fixed
    assert 'id="builder-logo-text"' in fixed


def test_leaves_page_untouched_when_span_already_live():
    html = ('<div id="builder"><img id="builder-logo" src="/media/logo.svg">'
            '<span class="h2 builder-logo-text" id="builder-logo-text"></span></div>')
    fixed, changed = ensure_logo_text(html)
    assert changed is False
    assert fixed == html


def test_reports_page_without_logo_image():
    """Без builder-logo вставлять подпись некуда — такую страницу трогать
    нельзя, её должен посмотреть человек."""
    html = '<div id="builder"><h2 id="builder-main-title"></h2></div>'
    fixed, changed = ensure_logo_text(html)
    assert changed is False
    assert fixed == html
