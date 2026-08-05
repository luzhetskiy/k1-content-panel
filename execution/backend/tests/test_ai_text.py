from types import SimpleNamespace

import pytest

from app.ai.text import LLMError, TextClient


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
