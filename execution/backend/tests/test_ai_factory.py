"""Фабрика клиентов RouterAI: дефолты, границы числа попыток и понятные
ошибки конфигурации вместо 401 от провайдера."""

import pytest

from app.ai.factory import (
    IMAGE_MAX_RETRIES,
    TEXT_MAX_RETRIES,
    AIConfigError,
    build_image_generator,
    build_text_client,
    image_params,
)
from app.config import config
from app.settings.service import SettingsService


@pytest.fixture
def service(db_session):
    from app.seed import seed_settings

    seed_settings(db_session)
    settings = SettingsService(db_session, config.encryption_key)
    settings.set_secret("routerai_api_key", "sk-test")
    return settings


def test_text_client_reads_settings(db_session, service):
    service.set("text_model", "anthropic/claude-opus-5")
    client = build_text_client(db_session)
    assert client.model == "anthropic/claude-opus-5"
    assert client.client.api_key == "sk-test"
    assert str(client.client.base_url).startswith("https://routerai.ru/api/v1")


def test_image_generator_reads_settings(db_session, service):
    service.set("image_model", "openai/gpt-image-3")
    generator = build_image_generator(db_session)
    assert generator.model == "openai/gpt-image-3"
    assert generator.api_key == "sk-test"
    assert generator.url == "https://routerai.ru/api/v1/images"


def test_missing_api_key_is_named_before_the_request(db_session):
    """openai-клиент принимает api_key="" молча (проверено: openai 1.59.6
    бракует только None) и уходит в запрос ради 401 — дорогой по времени
    способ узнать, что поле просто не заполнено."""
    from app.seed import seed_settings

    seed_settings(db_session)
    with pytest.raises(AIConfigError) as exc:
        build_text_client(db_session)
    assert "routerai_api_key" in str(exc.value)
    with pytest.raises(AIConfigError):
        build_image_generator(db_session)


def test_wrong_encryption_key_becomes_config_error(db_session, service):
    """SettingsService бросает SecretDecryptionError; вызывающим (API и
    Celery) удобнее один тип ошибки конфигурации с готовым текстом."""
    from cryptography.fernet import Fernet

    original = config.encryption_key
    config.encryption_key = Fernet.generate_key().decode()
    try:
        with pytest.raises(AIConfigError) as exc:
            build_text_client(db_session)
    finally:
        config.encryption_key = original
    assert "ENCRYPTION_KEY" in str(exc.value)


def test_retries_setting_is_applied(db_session, service):
    service.set("llm_max_retries", "2")
    assert build_text_client(db_session).max_retries == 2
    assert build_image_generator(db_session).max_retries == 2


def test_retries_are_bounded_by_time_budget(db_session, service):
    """llm_max_retries проверяется в админке только как «целое число».
    0 превратил бы _call в цикл без единой итерации (мгновенный «LLM
    недоступна после 0 попыток»), а завышенное значение выносит одну статью
    за ARTICLE_TIME_BUDGET_SECONDS = 900 с."""
    service.set("llm_max_retries", "99")
    assert build_text_client(db_session).max_retries == TEXT_MAX_RETRIES
    assert build_image_generator(db_session).max_retries == IMAGE_MAX_RETRIES

    service.set("llm_max_retries", "0")
    assert build_text_client(db_session).max_retries == 1
    assert build_image_generator(db_session).max_retries == 1


def test_image_retries_stay_within_documented_budget():
    """Худший случай пачки картинок посчитан в app/ai/images.py при двух
    попытках: 180 с × 2 + пауза 5 с = 365 с. Общий llm_max_retries = 3 дал бы
    555 с и вместе с текстовыми вызовами вышел бы за бюджет статьи."""
    from app.ai.images import TIMEOUT

    assert TIMEOUT * IMAGE_MAX_RETRIES + 5 * sum(range(1, IMAGE_MAX_RETRIES)) <= 365


def test_broken_retries_setting_falls_back_to_default(db_session, service):
    """Значение могли править прямо в БД, минуя валидацию админки."""
    service.set("llm_max_retries", "три")
    assert build_text_client(db_session).max_retries == TEXT_MAX_RETRIES


def test_explicit_max_retries_wins(db_session, service):
    assert build_text_client(db_session, max_retries=1).max_retries == 1


def test_empty_setting_falls_back_to_default(db_session, service):
    """Админ стёр поле в форме: openai-клиент принимает base_url="" молча и
    падает уже на запросе невнятным «Invalid URL»."""
    service.set("routerai_base_url", "")
    service.set("text_model", "")
    client = build_text_client(db_session)
    assert str(client.client.base_url).startswith("https://routerai.ru/api/v1")
    assert client.model == "anthropic/claude-sonnet-4-6"


def test_image_params_keys_match_builder(db_session, service):
    """Ключи потребляет ArticleBuilder (Task 16): size, quality, workers."""
    assert set(image_params(db_session)) == {"size", "quality", "workers"}
    assert image_params(db_session) == {"size": "1536x1024", "quality": "medium",
                                        "workers": 4}


def test_image_workers_bounded(db_session, service):
    """ThreadPoolExecutor(max_workers=0) — необработанный ValueError внутри
    Celery-задачи; границы берутся из INT_RANGES, а не из литералов здесь."""
    service.set("image_workers", "0")
    assert image_params(db_session)["workers"] == 1
    service.set("image_workers", "40")
    assert image_params(db_session)["workers"] == 8
