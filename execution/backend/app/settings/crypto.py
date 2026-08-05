from cryptography.fernet import Fernet, InvalidToken

# Ниже этой длины показывать голову и хвост нельзя: у 8-символьного значения
# «три первых плюс четыре последних» скрывает ровно один символ.
MIN_MASKABLE_LEN = 12


class SecretDecryptionError(RuntimeError):
    """Значение не расшифровывается текущим ключом — обычно ENCRYPTION_KEY
    сменили, а настройки в БД остались зашифрованными старым."""


def _fernet(key: str) -> Fernet:
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        # Пустой ключ в проде исключён (${ENCRYPTION_KEY:?...} в compose), но
        # обрезанный или не-base64 (например, вставили hex) — нет: он
        # проходит проверку compose и падает именно здесь, ровно на том
        # экране, куда админ идёт разбираться.
        raise SecretDecryptionError(
            "ENCRYPTION_KEY невалиден: ожидаются 32 url-safe base64-байта"
        ) from exc


def encrypt(value: str, key: str) -> str:
    return _fernet(key).encrypt(value.encode()).decode()


def decrypt(value: str, key: str) -> str:
    try:
        return _fernet(key).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise SecretDecryptionError("значение зашифровано другим ключом") from exc


def mask(value: str) -> str:
    """Для отдачи в API: узнаваемо, но бесполезно для злоупотребления."""
    if len(value) < MIN_MASKABLE_LEN:
        return "***"
    return f"{value[:3]}...{value[-4:]}"
