"""Сборка клиентов RouterAI из настроек в БД. Одна точка — чтобы задачи Celery
и API-эндпоинты не читали настройки каждый по-своему."""

import logging

from sqlalchemy.orm import Session

from app.ai.images import ImageGenerator
from app.ai.text import TextClient, build_client
from app.config import config
from app.seed import DEFAULT_SETTINGS, INT_RANGES
from app.settings.crypto import SecretDecryptionError
from app.settings.service import SettingsService

logger = logging.getLogger(__name__)

# Верхняя граница числа попыток на один вызов. llm_max_retries приходит из
# админки, где проверяется только «целое число» (INT_KEYS в app/seed.py), а
# длительность вызова упирается в бюджет статьи (ARTICLE_TIME_BUDGET_SECONDS
# = 900 с, Task 18):
#   текст:    REQUEST_TIMEOUT_SECONDS=120 × 3 + паузы backoff(2, 4) = 366 с;
#   картинки: TIMEOUT=180 × 2 + пауза 5 с = 365 с на пачку (генерируются
#             параллельно, см. app/ai/images.py — там этот расчёт и записан).
# Отсюда разные потолки: третья попытка картинки — это 555 с вместо 365 и,
# вместе с текстовыми вызовами той же статьи, гарантированный выход за
# бюджет. Нижняя граница — 1: при 0 цикл в TextClient._call не делает ни
# одной итерации и сразу отдаёт «LLM недоступна после 0 попыток».
TEXT_MAX_RETRIES = 3
IMAGE_MAX_RETRIES = 2

# Пауза между попытками текстового вызова. Держим здесь, а не в TextClient:
# дефолт класса (0.0) удобен тестам, боевое значение — вызывающему.
TEXT_BACKOFF_SECONDS = 2.0


class AIConfigError(RuntimeError):
    """Настройки не позволяют собрать клиента: нет ключа или он зашифрован
    другим ENCRYPTION_KEY. Отдельно от LLMError намеренно — это ошибка
    конфигурации панели (чинится в админке, HTTP 400), а не отказ RouterAI
    (HTTP 502)."""


def _service(db: Session) -> SettingsService:
    return SettingsService(db, config.encryption_key)


def _setting(service: SettingsService, key: str) -> str:
    """Дефолт берётся из DEFAULT_SETTINGS, а не из литерала на месте вызова:
    иначе один и тот же дефолт живёт в двух местах и расходится при первой же
    смене модели. Пустая строка (админ стёр поле в форме) — тоже дефолт:
    openai-клиент принимает base_url="" молча (проверено на openai 1.59.6),
    а падает потом на запросе невнятным «Invalid URL»."""
    return service.get_str(key, DEFAULT_SETTINGS[key]) or DEFAULT_SETTINGS[key]


def _api_key(service: SettingsService) -> str:
    try:
        key = service.get_secret("routerai_api_key")
    except SecretDecryptionError as exc:
        raise AIConfigError(str(exc)) from exc
    if not key:
        # openai-клиент бракует только api_key=None (проверено на openai
        # 1.59.6), а с пустой строкой уходит в запрос ради 401 — дорогой по
        # времени способ узнать, что поле просто не заполнено.
        raise AIConfigError(
            "ключ RouterAI не задан — заполните routerai_api_key в настройках")
    return key


def _retries(service: SettingsService, limit: int, override: int | None = None) -> int:
    if override is not None:
        return max(1, min(limit, override))
    default = int(DEFAULT_SETTINGS["llm_max_retries"])
    try:
        raw = service.get_int("llm_max_retries", default)
    except ValueError:
        # Через админку не пройдёт (INT_KEYS), но значение могли править
        # прямо в БД. Необработанный ValueError здесь означал бы 500 на
        # ровном месте — и в API, и в Celery-задаче.
        logger.warning("llm_max_retries не число — используем %s", default)
        return default
    value = max(1, min(limit, raw))
    if value != raw:
        logger.warning("llm_max_retries=%s вне диапазона 1..%s — используем %s",
                       raw, limit, value)
    return value


def build_text_client(db: Session, max_retries: int | None = None) -> TextClient:
    """max_retries переопределяется вызывающим для интерактивных прогонов
    (экран «Промпты», Task 13): три попытки — это до 366 с ожидания в
    синхронном HTTP-запросе."""
    service = _service(db)
    client = build_client(_setting(service, "routerai_base_url"), _api_key(service))
    return TextClient(
        client,
        model=_setting(service, "text_model"),
        max_retries=_retries(service, TEXT_MAX_RETRIES, max_retries),
        backoff=TEXT_BACKOFF_SECONDS,
    )


def build_image_generator(db: Session) -> ImageGenerator:
    service = _service(db)
    return ImageGenerator(
        base_url=_setting(service, "routerai_base_url"),
        api_key=_api_key(service),
        model=_setting(service, "image_model"),
        max_retries=_retries(service, IMAGE_MAX_RETRIES),
    )


def image_params(db: Session) -> dict:
    """Ключи потребляет ArticleBuilder (Task 16): size, quality, workers."""
    service = _service(db)
    low, high = INT_RANGES["image_workers"]
    try:
        workers = service.get_int("image_workers", int(DEFAULT_SETTINGS["image_workers"]))
    except ValueError:
        workers = int(DEFAULT_SETTINGS["image_workers"])
    return {
        "size": _setting(service, "image_size"),
        "quality": _setting(service, "image_quality"),
        # Границы — те же, что проверяет админка (INT_RANGES), а не литералы
        # здесь: ThreadPoolExecutor(max_workers=0) валит Celery-задачу
        # необработанным ValueError, а значение могли править прямо в БД.
        "workers": max(low, min(high, workers)),
    }
