from sqlalchemy.exc import IntegrityError
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

    def _row(self, name: str) -> Setting | None:
        return self.db.get(Setting, name)

    def _raw(self, name: str) -> str | None:
        row = self._row(name)
        return row.value if row else None

    def _upsert(self, name: str, value: str, is_secret: bool, commit: bool) -> None:
        row = self._row(name)
        if row is not None:
            row.value = value
            row.is_secret = is_secret
        else:
            self.db.add(Setting(key=name, value=value, is_secret=is_secret))
        if not commit:
            return
        try:
            self.db.commit()
        except IntegrityError:
            # Конкурентная первая запись: между нашим SELECT (промах) и
            # INSERT кто-то другой уже вставил строку с этим key — не только
            # фоновые воркеры, Task 6 зовёт seed_settings на каждом GET, так
            # что два админа, открывшие страницу настроек на пустой БД,
            # сталкиваются здесь же. К моменту повтора строка уже есть —
            # оставшийся путь превращает его в UPDATE. Повторяем один раз;
            # если и это не помогло — поднимаем настоящую причину.
            self.db.rollback()
            row = self._row(name)
            row.value = value
            row.is_secret = is_secret
            self.db.commit()

    def set(self, name: str, value: str, commit: bool = True) -> None:
        self._upsert(name, value, is_secret=False, commit=commit)

    def set_secret(self, name: str, value: str, commit: bool = True) -> None:
        self._upsert(name, encrypt(value, self.key), is_secret=True, commit=commit)

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
        row = self._row(name)
        if row is None:
            return default
        if row.is_secret:
            # is_secret не читается боевым кодом Task 6 (там своя константа
            # SECRET_KEYS), но эта проверка закрывает класс утечек, если
            # get_str когда-нибудь вызовут по секретному ключу по ошибке —
            # без неё вызывающий тихо получил бы шифротекст.
            raise SecretDecryptionError(
                f"настройка {name!r} хранится зашифрованной — используйте get_secret()"
            )
        return row.value

    def get_int(self, name: str, default: int) -> int:
        raw = self._raw(name)
        return int(raw) if raw is not None else default

    def get_bool(self, name: str, default: bool) -> bool:
        raw = self._raw(name)
        return raw.lower() in ("1", "true", "yes") if raw is not None else default
