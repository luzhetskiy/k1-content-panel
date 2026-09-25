from types import SimpleNamespace

from app.category_meta.products import products_by_category
from app.category_meta.seo_text import clean_seo_html, plain_text, validate_seo_text
from app.category_meta.validate import MetaContext


def ctx(**overrides):
    values = dict(form_nominative="фанера", form_buy="купить фанеру", chosen_form="nominative",
                  city="Москва", city_in="в Москве", brand="Стройбаза", stoplist=["леруа"],
                  site_description="Интернет-магазин стройматериалов, доставка по Москве")
    values.update(overrides)
    return MetaContext(**values)


def text_of(size, extra=""):
    block = "<h2>Фанера в Москве</h2><p>Какую фанеру выбрать для пола и стен." + extra + "</p>"
    html = block
    while len(plain_text(html)) < size:
        html += block
    return html


def test_clean_keeps_text_markup_only():
    raw = ('```html\n<h1 class="x">Фанера</h1><p style="color:red">Лист <a href="/x">ФК</a>'
           '<b>18 мм</b></p><script>alert(1)</script><ul><li>ФСФ</li></ul>\n```')
    assert clean_seo_html(raw) == ("<h2>Фанера</h2><p>Лист ФК<strong>18 мм</strong></p>"
                                   "<ul><li>ФСФ</li></ul>")


def test_plain_text_length_ignores_tags_and_entities():
    assert plain_text("<h2>Фанера</h2>\n<p>ФК&nbsp;и&nbsp;ФСФ</p>") == "Фанера ФК и ФСФ"


def test_valid_text_passes():
    assert validate_seo_text(text_of(2600), ctx()) == []


def test_length_has_tolerance_around_target():
    assert validate_seo_text(text_of(2310), ctx()) == []
    short = validate_seo_text(text_of(1000), ctx())
    assert len(short) == 1 and short[0].startswith("SEO-текст должен быть 2500–3000 символов")
    assert validate_seo_text(text_of(3400), ctx())


def test_required_phrase_city_and_headings():
    html = "<p>" + "Листовой материал для пола. " * 100 + "</p>"
    assert validate_seo_text(html, ctx()) == [
        "в SEO-тексте нет подзаголовков <h2>", "в SEO-тексте нет фразы «фанера»",
        "в SEO-тексте нет «в Москве»"]


def test_claims_and_stoplist():
    errors = validate_seo_text(text_of(2600, " Всё в наличии, как в Леруа."), ctx())
    assert "в SEO-тексте утверждение «в наличии», которого нет в описании сайта" in errors
    assert "в SEO-тексте слова из стоп-листа: леруа" in errors


def row(id_, remote_id, name, parent=None):
    return SimpleNamespace(id=id_, remote_id=remote_id, name=name, remote_parent_id=parent)


def test_products_by_category_includes_subcategories():
    rows = [row(1, 45, "Листовые материалы"), row(2, 46, "Фанера", 45), row(3, 47, "ОСБ", 45),
            row(4, 90, "Пустая")]
    products = [
        {"name": "Фанера  ФК 10 мм", "categories": ["Фанера"], "published": True},
        {"name": "Фанера ФСФ 18 мм", "categories": ["фанера"], "published": False},
        {"name": "OSB-3 12 мм", "categories": ["ОСБ", "Листовые материалы"], "published": True},
    ]
    names = products_by_category(products, rows)
    assert names[2] == ["Фанера ФК 10 мм"]
    assert names[1] == ["OSB-3 12 мм", "Фанера ФК 10 мм"]
    assert names[4] == []
