from unittest.mock import Mock, patch

import pytest
import requests

from app.companies.scrape import ScrapeError, fetch_company_text


def test_fetch_company_text_extracts_readable_text():
    html = "<html><head><title>ООО Дом</title><style>x{}</style></head>" \
          "<body><script>1</script><p>Строим дома под ключ.</p></body></html>"
    response = Mock(text=html)
    response.raise_for_status = Mock()
    with patch("app.companies.scrape.requests.get", return_value=response):
        text = fetch_company_text("https://dom.ru")
    assert "TITLE: ООО Дом" in text
    assert "Строим дома под ключ." in text
    assert "1" not in text.split("\n")   # содержимое <script> вырезано


def test_fetch_company_text_truncates_to_limit():
    html = "<html><body><p>" + "а" * 20_000 + "</p></body></html>"
    response = Mock(text=html)
    response.raise_for_status = Mock()
    with patch("app.companies.scrape.requests.get", return_value=response):
        text = fetch_company_text("https://dom.ru")
    assert len(text) <= 12_100   # запас на "TITLE: \n\n"


def test_fetch_company_text_wraps_network_error():
    with patch("app.companies.scrape.requests.get",
              side_effect=requests.ConnectionError("boom")):
        with pytest.raises(ScrapeError):
            fetch_company_text("https://dom.ru")


def test_fetch_company_text_wraps_http_error():
    response = Mock(text="<html></html>")
    response.raise_for_status = Mock(side_effect=requests.HTTPError("404"))
    with patch("app.companies.scrape.requests.get", return_value=response):
        with pytest.raises(ScrapeError):
            fetch_company_text("https://dom.ru")


def test_fetch_company_text_strips_header_and_footer():
    html = "<html><body><header><a>Меню</a></header><p>Строим дома.</p>" \
          "<footer>© 2024</footer></body></html>"
    response = Mock(text=html)
    response.raise_for_status = Mock()
    with patch("app.companies.scrape.requests.get", return_value=response):
        text = fetch_company_text("https://dom.ru")
    assert "Меню" not in text
    assert "© 2024" not in text
