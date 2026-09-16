import json

import pytest
import requests

from app.wordstat.client import (
    WordstatAuthError, WordstatClient, WordstatError, parse_top,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", json_error=False):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload if payload is not None else {}
        self._json_error = json_error
        self.text = text

    def json(self):
        if self._json_error:
            raise json.JSONDecodeError("Expecting value", self.text, 0)
        return self._payload


def scripted_post(monkeypatch, responses):
    """Каждый вызов requests.post отдаёт следующий элемент; исключение — поднимается."""
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        item = responses[min(len(calls), len(responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("app.wordstat.client.requests.post", fake_post)
    return calls


def client(sleeps=None):
    return WordstatClient("KEY", sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_top_requests_sends_key_region_and_phrase_count(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(payload={"totalCount": "5"})])
    assert client().top_requests("фанера", 213, 300) == {"totalCount": "5"}
    call = calls[0]
    assert call["url"] == "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"
    assert call["headers"] == {"Authorization": "Api-Key KEY"}
    assert call["json"] == {"phrase": "фанера", "numPhrases": "300", "regions": ["213"]}
    assert call["timeout"] == 30


def test_top_requests_without_region_omits_regions(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse()])
    client().top_requests("фанера", None, 1)
    assert "regions" not in calls[0]["json"]


def test_regions_tree_posts_empty_body(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(payload={"regions": []})])
    assert client().regions_tree() == {"regions": []}
    # Метод называется getRegionsTree: «regionsTree» отвечает 404 (проверено 2026-09-16).
    assert calls[0]["url"] == "https://searchapi.api.cloud.yandex.net/v2/wordstat/getRegionsTree"
    assert calls[0]["json"] == {}


def test_auth_error_is_not_retried(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(401, text="bad key")])
    with pytest.raises(WordstatAuthError) as err:
        client().top_requests("фанера", 213)
    assert len(calls) == 1
    assert err.value.status_code == 401
    assert "wordstat_api_key" in str(err.value)


def test_bad_request_is_not_retried(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(400, text="phrase too long")])
    with pytest.raises(WordstatError) as err:
        client().top_requests("фанера", 213)
    assert len(calls) == 1 and err.value.status_code == 400
    assert not isinstance(err.value, WordstatAuthError)


def test_rate_limit_is_retried_with_backoff(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(429), FakeResponse(payload={"ok": 1})])
    sleeps = []
    assert client(sleeps).top_requests("фанера", 213) == {"ok": 1}
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_server_error_gives_up_after_three_attempts(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(503, text="down")])
    sleeps = []
    with pytest.raises(WordstatError) as err:
        client(sleeps).top_requests("фанера", 213)
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]
    assert err.value.status_code == 503


def test_network_failure_is_retried_and_reported(monkeypatch):
    calls = scripted_post(monkeypatch, [requests.ConnectionError("dns")])
    with pytest.raises(WordstatError) as err:
        client().top_requests("фанера", 213)
    assert len(calls) == 3
    assert err.value.status_code is None
    assert "сеть недоступна" in str(err.value)


def test_non_json_body_is_retried(monkeypatch):
    calls = scripted_post(monkeypatch, [FakeResponse(json_error=True, text="<html>"),
                                        FakeResponse(payload={"ok": 1})])
    assert client().top_requests("фанера", 213) == {"ok": 1}
    assert len(calls) == 2


def test_parse_top_converts_strings():
    result = parse_top({"totalCount": "96275",
                        "results": [{"phrase": "фанера", "count": "96275"},
                                    {"phrase": "фанера купить", "count": "12238"},
                                    {"phrase": "", "count": "1"}]})
    assert result.total_count == 96275
    assert result.phrases == [("фанера", 96275), ("фанера купить", 12238)]


def test_parse_top_empty_body():
    result = parse_top({})
    assert result.total_count == 0 and result.phrases == []
