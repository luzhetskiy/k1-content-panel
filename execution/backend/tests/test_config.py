from app.config import Config


def test_defaults_are_dev_friendly(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    cfg = Config(_env_file=None)
    assert cfg.database_url.startswith("postgresql+psycopg://")
    assert cfg.redis_url.startswith("redis://")
    assert cfg.cookie_secure is False


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "from-env")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    cfg = Config()
    assert cfg.jwt_secret == "from-env"
    assert cfg.cookie_secure is True
