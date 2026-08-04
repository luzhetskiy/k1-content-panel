from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

# Логирование настраивается до импорта app.*: fileConfig(disable_existing_loggers=True)
# по умолчанию глушит все уже созданные логгеры — если бы app.config/app.db успели
# завести свои до этого вызова, они замолчали бы молча и без предупреждения.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.config import config as app_config
from app.db import Base
import app.models  # noqa: F401 — регистрирует все модели в Base.metadata

# `%` в пароле БД (типичный символ в base64-паролях) иначе интерпретируется
# ConfigParser'ом как начало интерполяции и роняет любую команду alembic ещё
# до обращения к БД — экранируем его перед записью в config.
config.set_main_option("sqlalchemy.url", app_config.database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
