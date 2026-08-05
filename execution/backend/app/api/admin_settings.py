from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.config import config
from app.models.user import User
from app.seed import DEFAULT_SETTINGS, INT_KEYS, INT_RANGES, SECRET_KEYS, seed_settings
from app.settings.crypto import SecretDecryptionError, mask
from app.settings.service import SettingsService

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])


def _service(db: Session) -> SettingsService:
    return SettingsService(db, config.encryption_key)


def _current_settings(db: Session) -> dict:
    """Собирает текущие настройки в ответ. Не вызывает seed_settings — тот
    вызывается ровно один раз за GET из read_settings, а не отсюда: PUT
    зовёт эту функцию после своего единственного коммита, и повторное
    наполнение дефолтов со своим отдельным commit() внутри было бы лишней
    точкой отказа поверх уже сохранённых изменений (если бы оно упало,
    клиент получил бы 500, хотя запрошенные им изменения уже записаны)."""
    service = _service(db)
    result = {key: service.get_str(key, default) for key, default in DEFAULT_SETTINGS.items()}
    errors: dict[str, str] = {}
    for key in SECRET_KEYS:
        try:
            value = service.get_secret(key)
        except SecretDecryptionError as exc:
            # Пустая строка, а не текст ошибки: это же поле уходит через PUT
            # обратно как «новое значение», если фронт когда-нибудь пришлёт
            # форму целиком, — а любая непустая строка в этом поле
            # шифруется и сохраняется как настоящий секрет (см. PUT ниже).
            # Раньше сюда клали f"ОШИБКА: {exc}" — при round-trip GET → PUT
            # это необратимо затирало настоящий ключ текстом диагностики.
            # Пустая строка уже означает «не менять» по контракту PUT, так
            # что round-trip перестаёт быть разрушительным по построению.
            # Сама диагностика уходит в отдельный ключ _errors, откуда её
            # точно не отправят обратно как значение.
            result[key] = ""
            errors[key] = str(exc)
            continue
        result[key] = mask(value) if value else ""
    if errors:
        result["_errors"] = errors
    return result


@router.get("")
def read_settings(db: Session = Depends(get_db),
                  _user: User = Depends(require_role("admin"))) -> dict:
    seed_settings(db)
    return _current_settings(db)


@router.put("")
def update_settings(payload: dict, db: Session = Depends(get_db),
                    _user: User = Depends(require_role("admin"))) -> dict:
    # int-настройки валидируются здесь, до записи — иначе опечатка проходит
    # с 200 и падает позже необработанным ValueError внутри celery-таски
    # (Task 8), где её уже никто не увидит. Часть из них дополнительно
    # ограничена диапазоном (INT_RANGES) — «целое число» само по себе не
    # спасает от 0 (ThreadPoolExecutor(max_workers=0) тоже падает необработанным
    # ValueError) или от опечатки вроде «40» вместо «4».
    errors = []
    for key, value in payload.items():
        if key in INT_KEYS:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                errors.append(f"настройка {key!r} должна быть целым числом")
                continue
            bounds = INT_RANGES.get(key)
            if bounds and not (bounds[0] <= parsed <= bounds[1]):
                errors.append(
                    f"настройка {key!r} должна быть целым числом "
                    f"от {bounds[0]} до {bounds[1]}")
    if errors:
        raise HTTPException(422, "; ".join(errors))

    service = _service(db)
    for key, value in payload.items():
        if key in SECRET_KEYS:
            # Пустая строка = «не менять»: фронт получает маску (или пустую
            # строку при ошибке расшифровки — см. _current_settings), а не
            # значение, и не может отправить секрет обратно неизменным.
            if value:
                service.set_secret(key, str(value), commit=False)
        elif key in DEFAULT_SETTINGS:
            service.set(key, str(value), commit=False)
    # Один коммит на весь payload: несколько ключей в одном PUT либо
    # применяются все разом, либо ни один — иначе ошибка на середине списка
    # оставляет половину настроек изменённой, а половину нет.
    db.commit()
    # _current_settings, а не read_settings(db, _user): без повторного
    # seed_settings и без вызова функции-эндпоинта из другого эндпоинта —
    # сборка ответа отделена от наполнения дефолтов.
    return _current_settings(db)
