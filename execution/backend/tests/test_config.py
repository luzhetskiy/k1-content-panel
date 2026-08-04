from app.config import Config


def test_defaults_are_dev_friendly():
    cfg = Config()
    assert cfg.database_url.startswith("postgresql+psycopg://")
    assert cfg.redis_url.startswith("redis://")
    assert cfg.cookie_secure is False


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "from-env")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    cfg = Config()
    assert cfg.jwt_secret == "from-env"
    assert cfg.cookie_secure is True
