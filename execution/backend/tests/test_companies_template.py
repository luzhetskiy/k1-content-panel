from app.companies.template import fill_builder_template

TEMPLATE = """
<div id="builder">
  <img id="builder-logo" src="" alt="">
  <span id="builder-logo-text"></span>
  <h1 id="builder-main-title"></h1>
  <div id="builder-about-company"><p></p></div>
  <div id="builder-specialization"><p></p></div>
  <div id="builder-contacts">
    <h2 id="builder-contacts-title"></h2>
    <div id="builder-contacts-grid">
      <div id="builder-contact-1">
        <a class="builder-line-address"><span class="circle-img"></span><p></p></a>
        <a class="builder-line-phone"><span class="circle-img"></span></a>
        <a class="builder-line-email"><span class="circle-img"></span></a>
        <a class="builder-line-time"><span class="circle-img"></span><p></p></a>
        <a class="builder-line-site"><span class="circle-img"></span></a>
        <a class="builder-line-note"><span class="circle-img"></span><p></p></a>
      </div>
    </div>
  </div>
</div>
"""


def _info(**over):
    base = dict(
        builder_name="ООО Дом", city_name="Самара", city_prepositional="Самаре",
        builder_logo_src="", builder_logo_alt="", about_company="Строим дома.",
        specialization="", projects_services="", benefits="",
        contacts=[{"address": "ул. Ленина 1", "phone_tel": "+78462770605",
                  "phone_text": "+7 846 277-06-05", "email": "info@dom.ru",
                  "working_hours": "9:00-18:00", "site_url": "https://dom.ru",
                  "site_text": "dom.ru"}],
        address="ул. Ленина 1", coordinates="",
    )
    base.update(over)
    return base


def test_fill_sets_title_and_about():
    html = fill_builder_template(TEMPLATE, _info())
    assert 'id="builder-main-title"' in html
    assert "О компании ООО Дом" in html
    assert "Строим дома." in html


def test_fill_drops_empty_about_block():
    html = fill_builder_template(TEMPLATE, _info(specialization=""))
    assert 'id="builder-specialization"' not in html


def test_fill_renders_contact_line():
    html = fill_builder_template(TEMPLATE, _info())
    assert 'href="tel:+78462770605"' in html
    assert "+7 846 277-06-05" in html
    assert 'href="mailto:info@dom.ru"' in html


def test_fill_uses_text_logo_fallback_when_no_logo_src():
    html = fill_builder_template(TEMPLATE, _info(builder_logo_src=""))
    assert "ООО Дом" in html
    assert 'id="builder-logo"' not in html   # img-логотип убран
