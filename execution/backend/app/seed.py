"""Дефолтные значения настроек и промптов. Идемпотентна: существующие
записи не перезаписываются — отредактированный в админке промпт переживает
перезапуск сервиса."""

from sqlalchemy.orm import Session

from app.models.setting import Setting

# Ключ RouterAI сюда не входит: у него нет осмысленного дефолта, он вводится
# админом и хранится зашифрованным.
DEFAULT_SETTINGS = {
    "routerai_base_url": "https://routerai.ru/api/v1",
    "text_model": "anthropic/claude-sonnet-4-6",
    "image_model": "openai/gpt-image-2",
    "image_quality": "medium",   # high дороже втрое: ≈16.8 против ≈5.4 за кадр
    "image_size": "1536x1024",
    "image_workers": "4",
    "llm_max_retries": "3",
}

SECRET_KEYS = {"routerai_api_key"}

# int-настройки валидируются на PUT до записи (см. admin_settings.py) —
# иначе опечатка проходит с 200 и падает позже необработанным ValueError
# внутри celery-таски (Task 8), где её уже никто не увидит.
INT_KEYS = {"image_workers", "llm_max_retries"}


def seed_settings(db: Session) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        if db.get(Setting, key) is None:
            db.add(Setting(key=key, value=value, is_secret=False))
    db.commit()
