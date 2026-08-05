from sqlalchemy.orm import Session

from app.models.setting import Setting
from app.settings.crypto import SecretDecryptionError, decrypt, encrypt


class SettingsService:
    """Единственная точка доступа к бизнес-настройкам.

    Обычные значения хранятся как есть, секреты — зашифрованными.
    """

    def __init__(self, db: Session, encryption_key: str):
        self.db = db
        self.key = encryption_key

    def _raw(self, name: str) -> str | None:
        row = self.db.get(Setting, name)
        return row.value if row else None

    def set(self, name: str, value: str) -> None:
        row = self.db.get(Setting, name) or Setting(key=name)
        row.value = value
        row.is_secret = False
        self.db.merge(row)
        self.db.commit()

    def set_secret(self, name: str, value: str) -> None:
        row = self.db.get(Setting, name) or Setting(key=name)
        row.value = encrypt(value, self.key)
        row.is_secret = True
        self.db.merge(row)
        self.db.commit()

    def get_secret(self, name: str, default: str = "") -> str:
        raw = self._raw(name)
        if not raw:
            return default
        try:
            return decrypt(raw, self.key)
        except SecretDecryptionError:
            # Молча вернуть default нельзя: сервис пойдёт в RouterAI без ключа
            # и получит невнятный 401 вместо причины.
            raise SecretDecryptionError(
                f"настройка {name!r} зашифрована другим ключом — проверь "
                f"ENCRYPTION_KEY или перезапиши значение через админку"
            ) from None

    def get_str(self, name: str, default: str = "") -> str:
        raw = self._raw(name)
        return raw if raw is not None else default

    def get_int(self, name: str, default: int) -> int:
        raw = self._raw(name)
        return int(raw) if raw is not None else default

    def get_bool(self, name: str, default: bool) -> bool:
        raw = self._raw(name)
        return raw.lower() in ("1", "true", "yes") if raw is not None else default
