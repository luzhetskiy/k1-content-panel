"""Сборка клиента Wordstat и его настроек из БД — по образцу app/ai/factory.py."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import config
from app.seed import DEFAULT_SETTINGS, INT_RANGES
from app.settings.crypto import SecretDecryptionError
from app.settings.service import SettingsService
from app.wordstat.client import WordstatClient

logger = logging.getLogger(__name__)


class WordstatConfigError(RuntimeError):
    """Ключ не задан или зашифрован другим ENCRYPTION_KEY — чинится в настройках."""


def _service(db: Session) -> SettingsService:
    return SettingsService(db, config.encryption_key)


def build_wordstat_client(db: Session) -> WordstatClient:
    try:
        key = _service(db).get_secret("wordstat_api_key")
    except SecretDecryptionError as exc:
        raise WordstatConfigError(str(exc)) from exc
    if not key:
        raise WordstatConfigError(
            "ключ Wordstat не задан — заполните wordstat_api_key в настройках")
    return WordstatClient(key)


def hourly_limit(db: Session) -> int:
    default = int(DEFAULT_SETTINGS["wordstat_hourly_limit"])
    low, high = INT_RANGES["wordstat_hourly_limit"]
    try:
        value = _service(db).get_int("wordstat_hourly_limit", default)
    except ValueError:
        logger.warning("wordstat_hourly_limit не число — используем %s", default)
        value = default
    return max(low, min(high, value))


def stoplist(db: Session) -> list[str]:
    raw = _service(db).get_str("meta_stoplist", DEFAULT_SETTINGS["meta_stoplist"])
    return [word.strip().casefold() for word in raw.split(",") if word.strip()]
