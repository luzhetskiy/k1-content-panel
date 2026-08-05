"""Текстовая часть RouterAI. Провайдер OpenAI-совместимый, поэтому смена
провайдера — это смена base_url и модели в настройках, без правки кода.
"""

import json
import logging
import re
import time
from dataclasses import dataclass

import openai
from openai import OpenAI

logger = logging.getLogger(__name__)

# Огороженный блок markdown: ```json ... ``` или ``` ... ```. Открывающая и
# закрывающая метки должны занимать свою строку целиком — иначе тройные
# кавычки, случайно оказавшиеся внутри значения (например, в примере кода
# внутри текста статьи), обрывали бы блок раньше времени.
_FENCE = re.compile(
    r"^```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n^```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

# Один вызов генерации текста статьи укладывается в это время с большим
# запасом; дефолт SDK (600 с на чтение) — это по сути «жди сколько хочешь»,
# а нам нужно, чтобы зависшее соединение не съедало слот Celery-воркера
# часами (см. app/celery_app.py — там свой предел на всю задачу).
REQUEST_TIMEOUT_SECONDS = 120.0

# Отказы, которые не изменятся при повторе: неверный ключ, нет доступа,
# некорректный запрос, модель/ресурс не найдены. Ретраить их — тратить время
# и попытки на заведомо тот же результат.
_NON_RETRYABLE = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.BadRequestError,
    openai.NotFoundError,
)


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
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=REQUEST_TIMEOUT_SECONDS,
        # Ретраями управляет TextClient. Если оставить дефолт SDK (2 внутренних
        # повтора), на каждую нашу попытку добавляются ещё до трёх HTTP-запросов
        # — при 429 это удваивает и без того лишнюю нагрузку на провайдера.
        max_retries=0,
    )


def _non_retryable_message(exc: Exception) -> str:
    if isinstance(exc, openai.AuthenticationError):
        return f"RouterAI отклонил ключ API — проверьте настройку routerai_api_key (401): {exc}"
    if isinstance(exc, openai.PermissionDeniedError):
        return f"RouterAI запретил доступ этим ключом (403): {exc}"
    if isinstance(exc, openai.BadRequestError):
        return f"RouterAI отклонил запрос как некорректный (400): {exc}"
    if isinstance(exc, openai.NotFoundError):
        return f"RouterAI не нашёл модель или ресурс (404): {exc}"
    return f"RouterAI отказал в запросе без права на повтор: {exc}"


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
        content = self._content(response)
        match = _FENCE.search(content)
        raw = match.group("body").strip() if match else content.strip()
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
            except _NON_RETRYABLE as exc:
                raise LLMError(_non_retryable_message(exc)) from exc
            except Exception as exc:
                # Сбои транспорта и превышение лимита частоты (429) — тут
                # повтор осмыслен, в отличие от _NON_RETRYABLE выше.
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

    def _usage(self, response) -> tuple[int, int, float]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0, 0, 0.0
        _missing = object()
        cost = getattr(usage, "cost", _missing)
        if cost is _missing:
            # usage.cost — расширение RouterAI, а не часть OpenAI API. Молчаливый
            # ноль неотличим от настоящего нуля, поэтому хотя бы в лог.
            logger.warning(
                "RouterAI не сообщил usage.cost для модели %s — стоимость записана как 0",
                self.model,
            )
            cost = 0.0
        return (
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
            float(cost or 0.0),
        )
