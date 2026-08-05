from cryptography.fernet import Fernet, InvalidToken

# Ниже этой длины показывать голову и хвост нельзя: у 8-символьного значения
# «три первых плюс четыре последних» скрывает ровно один символ.
MIN_MASKABLE_LEN = 12


class SecretDecryptionError(RuntimeError):
    """Значение не расшифровывается текущим ключом — обычно ENCRYPTION_KEY
    сменили, а настройки в БД остались зашифрованными старым."""


def encrypt(value: str, key: str) -> str:
    return Fernet(key.encode()).encrypt(value.encode()).decode()


def decrypt(value: str, key: str) -> str:
    try:
        return Fernet(key.encode()).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise SecretDecryptionError("значение зашифровано другим ключом") from exc


def mask(value: str) -> str:
    """Для отдачи в API: узнаваемо, но бесполезно для злоупотребления."""
    if len(value) < MIN_MASKABLE_LEN:
        return "***"
    return f"{value[:3]}...{value[-4:]}"
