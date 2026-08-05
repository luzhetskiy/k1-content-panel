"""Дефолтные значения настроек и промптов. Идемпотентна: существующие
записи не перезаписываются — отредактированный в админке промпт переживает
перезапуск сервиса."""

from sqlalchemy.exc import IntegrityError
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

# Диапазоны для тех int-настроек, которым мало быть просто целым числом.
# image_workers — ширина ThreadPoolExecutor(max_workers=...) в Task 8/16:
# 0 и отрицательные валят Celery-таску необработанным ValueError (max_workers
# must be greater than 0), а сверху границы не было вовсе — опечатка вроде
# «40» вместо «4» линейно растит число потоков и память на каждую партию
# статей. 8 — с запасом выше практического максимума, 1 — минимум, при
# котором параллелизм просто вырождается в последовательную генерацию.
# Данные, а не условие в роутере: следующие настройки с границами
# добавляются сюда же, без правки app/api/admin_settings.py.
INT_RANGES = {"image_workers": (1, 8)}


def seed_settings(db: Session) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        if db.get(Setting, key) is None:
            db.add(Setting(key=key, value=value, is_secret=False))
    try:
        db.commit()
    except IntegrityError:
        # Конкурентный seed_settings: вызывается на каждом GET
        # /api/admin/settings, так что два админа, открывшие страницу
        # настроек на пустой БД одновременно, оба проходят SELECT-фазу
        # (видят пусто) раньше, чем кто-то из них коммитит — тот же класс
        # гонки, что чинили в Task 5 для SettingsService._upsert. Один из
        # них коммитит первым и захватывает часть или все дефолтные ключи;
        # наш commit() ловит конфликт первичного ключа. Откатываем и
        # смотрим заново: то, что конкурент уже вставил, теперь видно и не
        # добавляется повторно (идемпотентность), то, что всё ещё
        # отсутствует — довставляем и коммитим один раз.
        db.rollback()
        for key, value in DEFAULT_SETTINGS.items():
            if db.get(Setting, key) is None:
                db.add(Setting(key=key, value=value, is_secret=False))
        db.commit()
