from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.config import config
from app.models.user import User
from app.seed import DEFAULT_SETTINGS, INT_KEYS, SECRET_KEYS, seed_settings
from app.settings.crypto import SecretDecryptionError, mask
from app.settings.service import SettingsService

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])


def _service(db: Session) -> SettingsService:
    return SettingsService(db, config.encryption_key)


@router.get("")
def read_settings(db: Session = Depends(get_db),
                  _user: User = Depends(require_role("admin"))) -> dict:
    seed_settings(db)
    service = _service(db)
    result = {key: service.get_str(key, default) for key, default in DEFAULT_SETTINGS.items()}
    for key in SECRET_KEYS:
        try:
            value = service.get_secret(key)
        except SecretDecryptionError as exc:
            result[key] = f"ОШИБКА: {exc}"
            continue
        result[key] = mask(value) if value else ""
    return result


@router.put("")
def update_settings(payload: dict, db: Session = Depends(get_db),
                    _user: User = Depends(require_role("admin"))) -> dict:
    # int-настройки валидируются здесь, до записи — иначе опечатка проходит
    # с 200 и падает позже необработанным ValueError внутри celery-таски
    # (Task 8), где её уже никто не увидит.
    errors = []
    for key, value in payload.items():
        if key in INT_KEYS:
            try:
                int(value)
            except (TypeError, ValueError):
                errors.append(f"настройка {key!r} должна быть целым числом")
    if errors:
        raise HTTPException(422, "; ".join(errors))

    service = _service(db)
    for key, value in payload.items():
        if key in SECRET_KEYS:
            # Пустая строка = «не менять»: фронт получает маску, а не значение,
            # и не может отправить секрет обратно неизменным.
            if value:
                service.set_secret(key, str(value), commit=False)
        elif key in DEFAULT_SETTINGS:
            service.set(key, str(value), commit=False)
    # Один коммит на весь payload: несколько ключей в одном PUT либо
    # применяются все разом, либо ни один — иначе ошибка на середине списка
    # оставляет половину настроек изменённой, а половину нет.
    db.commit()
    return read_settings(db, _user)
