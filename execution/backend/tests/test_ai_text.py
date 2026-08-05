import logging
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.ai.text import REQUEST_TIMEOUT_SECONDS, LLMError, TextClient, build_client


class FakeCompletions:
    def __init__(self, content, usage_cost=0.5, fail_times=0):
        self.content = content
        self.usage_cost = usage_cost
        self.fail_times = fail_times
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("transport down")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, cost=self.usage_cost),
        )


def fake_client(content, **kwargs):
    completions = FakeCompletions(content, **kwargs)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def _api_error(cls, status_code: int, message: str):
    """Настоящий экземпляр исключения openai.* — не заглушка, чтобы проверить
    именно ветвление по классам, которое видит боевой код."""
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(status_code, request=request, json={"error": {"message": message}})
    return cls(message, response=response, body=None)


class ExceptionCompletions:
    """Бросает заданное исключение первые `fail_times` вызовов, затем отвечает успешно."""

    def __init__(self, make_exc, fail_times=1, content="ок"):
        self.make_exc = make_exc
        self.fail_times = fail_times
        self.content = content
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.make_exc()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, cost=0.1),
        )


def test_complete_text_returns_content_and_usage():
    client, _ = fake_client("<p>Текст статьи</p>")
    result = TextClient(client, "test-model").complete_text("промпт")
    assert result.text == "<p>Текст статьи</p>"
    assert result.tokens_prompt == 10
    assert result.tokens_completion == 20
    assert result.cost == 0.5


def test_complete_json_strips_code_fence():
    client, _ = fake_client('```json\n["Тема 1", "Тема 2"]\n```')
    result = TextClient(client, "test-model").complete_json("промпт")
    assert result.data == ["Тема 1", "Тема 2"]


def test_complete_json_rejects_non_json():
    client, _ = fake_client("извините, не могу")
    with pytest.raises(LLMError, match="не JSON"):
        TextClient(client, "test-model").complete_json("промпт")


def test_transport_failure_is_retried():
    client, completions = fake_client("ок", fail_times=2)
    result = TextClient(client, "test-model", max_retries=3).complete_text("промпт")
    assert result.text == "ок"
    assert completions.calls == 3


def test_gives_up_after_max_retries():
    client, _ = fake_client("ок", fail_times=5)
    with pytest.raises(LLMError, match="недоступна"):
        TextClient(client, "test-model", max_retries=3).complete_text("промпт")


def test_empty_content_is_an_error():
    client, _ = fake_client(None)
    with pytest.raises(LLMError, match="пустой content"):
        TextClient(client, "test-model").complete_text("промпт")


# --- избирательный ретрай: детерминированные отказы vs временные ---

def test_auth_error_is_not_retried():
    completions = ExceptionCompletions(
        lambda: _api_error(openai.AuthenticationError, 401, "invalid api key"))
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    with pytest.raises(LLMError, match="ключ"):
        TextClient(client, "test-model", max_retries=3).complete_text("промпт")
    assert completions.calls == 1


def test_bad_request_is_not_retried():
    completions = ExceptionCompletions(
        lambda: _api_error(openai.BadRequestError, 400, "malformed request"))
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    with pytest.raises(LLMError, match="некорректный"):
        TextClient(client, "test-model", max_retries=3).complete_text("промпт")
    assert completions.calls == 1


def test_rate_limit_is_retried():
    completions = ExceptionCompletions(
        lambda: _api_error(openai.RateLimitError, 429, "too many requests"), fail_times=2)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    result = TextClient(client, "test-model", max_retries=3).complete_text("промпт")
    assert result.text == "ок"
    assert completions.calls == 3


def test_build_client_sets_timeout_and_disables_sdk_retries():
    client = build_client("https://routerai.ru/api/v1", "sk-test")
    assert client.timeout == REQUEST_TIMEOUT_SECONDS
    assert client.max_retries == 0


# --- разбор JSON с текстом вокруг огороженного блока ---

def test_complete_json_handles_text_before_and_after_fence():
    client, _ = fake_client('Вот темы:\n```json\n["Тема 1", "Тема 2"]\n```\nСпасибо!')
    result = TextClient(client, "test-model").complete_json("промпт")
    assert result.data == ["Тема 1", "Тема 2"]


def test_complete_json_takes_first_of_several_fences():
    client, _ = fake_client('```json\n["A"]\n```\n```json\n["B"]\n```')
    result = TextClient(client, "test-model").complete_json("промпт")
    assert result.data == ["A"]


def test_complete_json_handles_fence_without_language_tag():
    client, _ = fake_client('```\n["Тема"]\n```')
    result = TextClient(client, "test-model").complete_json("промпт")
    assert result.data == ["Тема"]


def test_complete_json_handles_plain_json_without_fence():
    client, _ = fake_client('["Тема 1", "Тема 2"]')
    result = TextClient(client, "test-model").complete_json("промпт")
    assert result.data == ["Тема 1", "Тема 2"]


def test_complete_json_keeps_triple_backticks_inside_string_intact():
    client, _ = fake_client('```json\n{"note": "see ```python``` block"}\n```')
    result = TextClient(client, "test-model").complete_json("промпт")
    assert result.data == {"note": "see ```python``` block"}


# --- расход, о котором провайдер не сообщил ---

def test_missing_usage_cost_logs_warning(caplog):
    client, _ = fake_client("ок")

    def create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ок"))],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=7),  # без cost
        )

    client.chat.completions.create = create
    with caplog.at_level(logging.WARNING):
        result = TextClient(client, "test-model").complete_text("промпт")
    assert result.cost == 0.0
    assert "usage.cost" in caplog.text
    assert "test-model" in caplog.text
