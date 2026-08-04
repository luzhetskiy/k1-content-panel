from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    database_url: str = "postgresql+psycopg://app:app@postgres:5432/content"
    redis_url: str = "redis://redis:6379/0"

    # Инфраструктурные секреты живут в окружении, а не в БД: их ротация не должна
    # зависеть от доступности БД при старте, а ENCRYPTION_KEY ещё и нужен, чтобы
    # прочитать саму таблицу настроек.
    jwt_secret: str = ""
    encryption_key: str = ""
    cookie_secure: bool = False

    media_dir: str = "/app/media"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


config = Config()
