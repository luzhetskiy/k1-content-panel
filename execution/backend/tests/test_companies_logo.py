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


def test_find_logo_skips_user_uploaded_media_even_with_logo_in_name():
    """Картинки из /wp-content/uploads/, /upload/, /media/uploads/ — это
    контент страницы (например, партнёрские логотипы в статье), а не
    логотип самой компании — см. execution/step2_find_svg_logos.py."""
    html = """
    <header>
      <img src="/wp-content/uploads/2024/logo-partner.png">
    </header>
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
