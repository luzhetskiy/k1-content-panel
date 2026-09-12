from unittest.mock import Mock, patch

import requests

from app.companies.logo import LogoCandidate, fetch_company_logo, find_logo


def test_find_logo_finds_img_with_logo_in_src_inside_header():
    html = """
    <header>
      <div class="nav"><img src="/images/other.png"></div>
      <img src="/static/logo-dom.png" alt="">
    </header>
    <img src="/static/decoy-logo.png">
    """
    assert find_logo(html, "https://dom.ru").url == "https://dom.ru/static/logo-dom.png"


def test_find_logo_finds_img_inside_logo_class_wrapper_via_its_own_alt():
    """Контейнер помечен только классом ("site-logo"), а сам <img> ловится
    по alt — так размечены реальные шаблоны (обёртка + картинка внутри)."""
    html = """
    <header>
      <div class="site-logo"><img src="/img/header1.png" alt="Logo"></div>
    </header>
    """
    assert find_logo(html, "https://dom.ru").url == "https://dom.ru/img/header1.png"


def test_find_logo_finds_wordpress_uploaded_logo():
    """skvlasov.ru: логотип лежит в /wp-content/uploads/. Старый фильтр по
    пути резал его вместе с партнёрскими картинками — теперь фильтруем по
    контейнеру, а не по каталогу."""
    html = """
    <header>
      <img src="/wp-content/uploads/2024/11/logo.png" alt="">
    </header>
    """
    assert find_logo(html, "https://skvlasov.ru").url == \
        "https://skvlasov.ru/wp-content/uploads/2024/11/logo.png"


def test_find_logo_skips_images_inside_partner_block():
    """Партнёрские логотипы в контенте страницы — не логотип самой компании."""
    html = """
    <div class="partners-list">
      <img src="/wp-content/uploads/2024/logo-partner.png" alt="Logo">
    </div>
    """
    assert find_logo(html, "https://dom.ru").url == ""


def test_find_logo_falls_back_to_whole_document_when_header_has_no_logo():
    html = """
    <header><nav>меню без картинок</nav></header>
    <div id="site-logo-wrap"><img src="/assets/logo-brand.png"></div>
    """
    assert find_logo(html, "https://dom.ru").url == "https://dom.ru/assets/logo-brand.png"


def test_find_logo_returns_empty_string_when_nothing_found():
    html = "<header><nav>меню без картинок</nav></header><p>текст</p>"
    assert find_logo(html, "https://dom.ru").url == ""


def test_find_logo_resolves_relative_src_against_base_url():
    html = '<header><img src="logo.png"></header>'
    assert find_logo(html, "https://dom.ru").url == "https://dom.ru/logo.png"


def test_find_logo_reads_lazy_attribute_when_src_is_placeholder():
    """sip-lider.ru: в src заглушка lazy.svg, настоящий логотип в data-lazy."""
    html = """
    <header>
      <img src="/local/templates/s/images/lazy.svg"
           data-lazy="/local/templates/s/images/logo_animate.svg" alt="">
    </header>
    """
    assert find_logo(html, "https://sip-lider.ru").url == \
        "https://sip-lider.ru/local/templates/s/images/logo_animate.svg"


def test_find_logo_ignores_data_uri_placeholder_in_src():
    """project-me.ru: в src прозрачный data:image/svg+xml, реальный файл в
    data-src. Раньше побеждала заглушка."""
    html = """
    <header>
      <img class="component-logo-img" alt="Logo"
           src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg'></svg>"
           data-src="/img/logo.png">
    </header>
    """
    assert find_logo(html, "https://project-me.ru").url == "https://project-me.ru/img/logo.png"


def test_find_logo_returns_nothing_when_only_data_uri_available():
    html = """
    <header>
      <img class="logo" src="data:image/svg+xml;utf8,<svg></svg>">
    </header>
    """
    assert find_logo(html, "https://dom.ru").url == ""


def test_find_logo_takes_first_url_from_srcset():
    html = """
    <header>
      <img class="logo" srcset="/img/logo.png 1x, /img/logo@2x.png 2x">
    </header>
    """
    assert find_logo(html, "https://dom.ru").url == "https://dom.ru/img/logo.png"


def test_logo_candidate_is_falsy_when_nothing_found():
    """Билдер проверяет результат в булевом контексте — пустой кандидат не
    должен считаться найденным логотипом."""
    assert not LogoCandidate()
    assert LogoCandidate(url="https://dom.ru/logo.png")
    assert LogoCandidate(svg_markup="<svg/>")


def test_fetch_company_logo_delegates_to_find_logo_on_success():
    response = Mock(text='<header><img src="/logo.png"></header>')
    response.raise_for_status = Mock()
    with patch("app.companies.logo.requests.get", return_value=response) as get:
        result = fetch_company_logo("https://dom.ru")
    get.assert_called_once()
    assert result.url == "https://dom.ru/logo.png"


def test_fetch_company_logo_returns_empty_string_on_request_error():
    with patch("app.companies.logo.requests.get",
              side_effect=requests.RequestException("timeout")):
        assert fetch_company_logo("https://dom.ru").url == ""
