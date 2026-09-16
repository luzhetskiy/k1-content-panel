import pytest

from app.config import config
from app.settings.service import SettingsService
from app.wordstat.factory import (
    WordstatConfigError, build_wordstat_client, hourly_limit, stoplist,
)


@pytest.fixture
def settings(db_session, monkeypatch):
    monkeypatch.setattr(config, "encryption_key", "8Bq3mA0kXqL2pR7vT1yZ4nC6wE9sU5hJ0dF2gK8lM3o=")
    return SettingsService(db_session, config.encryption_key)


def test_client_requires_key(db_session, settings):
    with pytest.raises(WordstatConfigError):
        build_wordstat_client(db_session)


def test_client_uses_decrypted_key(db_session, settings):
    settings.set_secret("wordstat_api_key", "AQVN-key")
    assert build_wordstat_client(db_session).api_key == "AQVN-key"


def test_hourly_limit_default_and_bounds(db_session, settings):
    assert hourly_limit(db_session) == 100
    settings.set("wordstat_hourly_limit", "0")
    assert hourly_limit(db_session) == 1
    settings.set("wordstat_hourly_limit", "abc")
    assert hourly_limit(db_session) == 100


def test_stoplist_default_and_custom(db_session, settings):
    assert "леруа" in stoplist(db_session)
    settings.set("meta_stoplist", " Леруа ,, Петрович ")
    assert stoplist(db_session) == ["леруа", "петрович"]
