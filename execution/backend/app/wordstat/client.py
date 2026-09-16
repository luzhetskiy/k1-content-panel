"""Клиент Yandex Search API — Wordstat.

Проверено 2026-09-16 (directions/2026-09-16-category-meta-design.md):
- POST {base}/topRequests и {base}/regionsTree, `Authorization: Api-Key ...`;
  folderId не нужен — папка берётся из сервисного аккаунта ключа;
- числа в ответе приходят строками;
- квота 100 запросов в час — считает её не клиент, а app/wordstat/quota.py.

Ретраи — здесь, в отличие от SiteClient: запросы только читающие, повтор
безопасен. Повторяются сетевые сбои, 429 и 5xx; 401/403 — WordstatAuthError
(ключ не подходит, запуск останавливается), прочие 4xx — сразу отказ.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

WORDSTAT_BASE_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat"
TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0


class WordstatError(RuntimeError):
    """status_code — HTTP-код; None для сетевых сбоев и ответа не-JSON."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class WordstatAuthError(WordstatError):
    """401/403: ключ не подходит — повтор не поможет."""


@dataclass
class TopResult:
    total_count: int
    phrases: list[tuple[str, int]]


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_top(body: dict) -> TopResult:
    phrases = [(str(item.get("phrase", "")).strip(), _int(item.get("count")))
               for item in (body.get("results") or []) if item.get("phrase")]
    return TopResult(total_count=_int(body.get("totalCount")), phrases=phrases)


class WordstatClient:
    def __init__(self, api_key: str, *, base_url: str = WORDSTAT_BASE_URL,
                 timeout: int = TIMEOUT_SECONDS, max_attempts: int = MAX_ATTEMPTS,
                 backoff: float = BACKOFF_SECONDS, sleep=time.sleep):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff = backoff
        self.sleep = sleep

    def top_requests(self, phrase: str, region_id: int | None, num_phrases: int = 300) -> dict:
        payload: dict = {"phrase": phrase, "numPhrases": str(num_phrases)}
        if region_id:
            payload["regions"] = [str(region_id)]
        return self._post("topRequests", payload)

    def regions_tree(self) -> dict:
        return self._post("regionsTree", {})

    def _post(self, method: str, payload: dict) -> dict:
        url = f"{self.base_url}/{method}"
        headers = {"Authorization": f"Api-Key {self.api_key}"}
        last_error = WordstatError(f"Wordstat {method}: не выполнено ни одной попытки")
        for attempt in range(self.max_attempts):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = WordstatError(
                    f"Wordstat {method}: сеть недоступна: {type(exc).__name__}: {exc}")
            else:
                if response.status_code in (401, 403):
                    raise WordstatAuthError(
                        f"ключ Wordstat отклонён — проверьте настройку wordstat_api_key "
                        f"(HTTP {response.status_code}): {response.text[:200]}",
                        response.status_code)
                if response.ok:
                    try:
                        return response.json()
                    except ValueError:
                        last_error = WordstatError(
                            f"Wordstat {method}: ответ не JSON: {response.text[:200]}")
                elif response.status_code == 429 or response.status_code >= 500:
                    last_error = WordstatError(
                        f"Wordstat {method}: HTTP {response.status_code}: {response.text[:200]}",
                        response.status_code)
                else:
                    raise WordstatError(
                        f"Wordstat {method}: HTTP {response.status_code}: {response.text[:200]}",
                        response.status_code)
            if attempt < self.max_attempts - 1:
                self.sleep(self.backoff * (2 ** attempt))
        raise last_error
