from app.house_projects.markup import (
    clean_model_html, find_description, plain_text, replace_description, same_html,
)

# Два вида разметки с сайта (stroybaza-samara.ru, страницы 16 и 189), сокращены.
INDENTED = """<section class="static-project">
<!-- Хар-ки и описание -->
<section class="product-info__section product-info__chars project-chars">
  <ul class="char"><li><span class="char-text">Площадь, м²</span><span class="char-text">82</span></li></ul>
</section>
<section class="product-info">
  <div class="product-info__main">
    <section class="product-info__section product-info__description project-description">
      <h2>Описание <br>проекта</h2>
      <div class="amk-projects__about-1 product-info__text">
        <p>Дом площадью 82.64 м² с террасой 9 м².</p><br>
        <h3>Ключевые преимущества <br>и удобства:</h3>
        <ul>
          <li>Монолитная плита.</li>
        </ul>
      </div>
    </section>
  </div>
</section>
<section class="cost-building"><div class="amk-projects__about-1" id="about-block-1">
{% custom_form "forma-rasschet" %}
</div></section>
</section>"""

ONE_LINE = ('<section class="static-project"><section class="product-info__section '
            'product-info__description project-description"><h2>Описание<br />\nпроекта</h2>'
            '<div class="amk-projects__about-1 product-info__text"><p>Каркасный дом 11×9 м.</p>'
            '<div class="note"><p>вложенный</p></div></div></section>'
            '<div class="buttons-columns"><a href="/addresses/projects/">Все проекты</a></div>'
            '</section>')


def test_finds_inner_html_of_description():
    block = find_description(INDENTED)
    assert block is not None
    assert block.inner_html.strip().startswith("<p>Дом площадью 82.64 м²")
    assert block.inner_html.strip().endswith("</ul>")


def test_nested_div_does_not_end_block_early():
    block = find_description(ONE_LINE)
    assert block.inner_html == ('<p>Каркасный дом 11×9 м.</p>'
                                '<div class="note"><p>вложенный</p></div>')


def test_replace_keeps_everything_outside_block_byte_for_byte():
    for page in (INDENTED, ONE_LINE):
        block = find_description(page)
        result = replace_description(page, block, "<p>Новый текст.</p>")
        assert result[:block.start] == page[:block.start]
        assert result.endswith(page[block.end:])
        assert find_description(result).inner_html == "\n<p>Новый текст.</p>\n"
    assert '{% custom_form "forma-rasschet" %}' in replace_description(
        INDENTED, find_description(INDENTED), "<p>x</p>")


def test_no_block_or_two_blocks_is_none():
    assert find_description("<section><p>нет описания</p></section>") is None
    twice = INDENTED.replace('<section class="cost-building">',
                             '<div class="product-info__text"><p>ещё</p></div>'
                             '<section class="cost-building">')
    assert find_description(twice) is None
    no_section = ONE_LINE.replace("project-description", "other")
    assert find_description(no_section) is None


def test_unclosed_block_is_none():
    assert find_description('<section class="project-description">'
                            '<div class="product-info__text"><p>обрыв') is None


def test_block_before_section_is_none():
    page = ('<div class="product-info__text"><p>x</p></div>'
            '<section class="project-description"><p>y</p></section>')
    assert find_description(page) is None


def test_clean_model_html_strips_fence():
    assert clean_model_html("```html\n<p>Текст</p>\n```") == "<p>Текст</p>"
    assert clean_model_html("Вот текст:\n```HTML\n<p>Текст</p>\n```\nГотово.") == "<p>Текст</p>"
    assert clean_model_html("  <p>Текст</p> ") == "<p>Текст</p>"
    assert clean_model_html(None) == ""


def test_plain_text_and_same_html():
    assert plain_text("<p>Дом&nbsp;82 м²</p>\n<ul><li>плита</li></ul>") == "Дом 82 м² плита"
    assert same_html("<p>a</p>\n  <p>b</p>", "<p>a</p><p>b</p>")
    assert same_html("<p>a<br />b</p>", "<p>a<br>b</p>")
    assert same_html("<p>«a» 11×9 b\xa0c</p>", "<p>&laquo;a&raquo; 11&times;9 b&nbsp;c</p>")
    assert not same_html("<p>a b</p>", "<p>ab</p>")
    assert not same_html("<p>a</p>", "<p>b</p>")
