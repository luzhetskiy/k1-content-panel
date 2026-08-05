"""Текстовая часть RouterAI. Провайдер OpenAI-совместимый, поэтому смена
провайдера — это смена base_url и модели в настройках, без правки кода.
"""

import json
import re
import time
from dataclasses import dataclass

from openai import OpenAI

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMError(RuntimeError):
    pass


@dataclass
class TextResult:
    text: str
    tokens_prompt: int
    tokens_completion: int
    cost: float


@dataclass
class JsonResult:
    data: object
    tokens_prompt: int
    tokens_completion: int
    cost: float


def build_client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key)


class TextClient:
    def __init__(self, client, model: str, max_retries: int = 3, backoff: float = 0.0,
                 temperature: float = 0.7):
        self.client = client
        self.model = model
        self.max_retries = max_retries
        self.backoff = backoff
        self.temperature = temperature

    def complete_text(self, prompt: str) -> TextResult:
        response = self._call(prompt)
        return TextResult(self._content(response), *self._usage(response))

    def complete_json(self, prompt: str) -> JsonResult:
        response = self._call(prompt)
        raw = _FENCE.sub("", self._content(response)).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"модель вернула не JSON: {raw[:200]}") from exc
        return JsonResult(data, *self._usage(response))

    def _call(self, prompt: str):
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self.temperature,
                )
            except Exception as exc:
                # Повторять имеет смысл только сбои транспорта. Разбор ответа
                # вынесен наружу, чтобы не ретраить осмысленный отказ модели.
                last_error = exc
                if self.backoff and attempt < self.max_retries - 1:
                    time.sleep(self.backoff * (2**attempt))
        raise LLMError(f"LLM недоступна после {self.max_retries} попыток: {last_error}")

    @staticmethod
    def _content(response) -> str:
        if not response.choices:
            raise LLMError("провайдер вернул ответ без вариантов")
        content = response.choices[0].message.content
        if content is None:
            raise LLMError("модель отказалась отвечать: пустой content")
        return content

    @staticmethod
    def _usage(response) -> tuple[int, int, float]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0, 0, 0.0
        return (
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
            float(getattr(usage, "cost", 0.0) or 0.0),
        )
