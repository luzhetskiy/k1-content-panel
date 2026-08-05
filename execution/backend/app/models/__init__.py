from app.models.setting import Setting
from app.models.user import User

# Единая точка регистрации моделей: alembic/env.py и tests/conftest.py делают
# `import app.models`, чтобы Base.metadata увидел все таблицы разом. Новую
# модель — добавляй сюда, а не в env.py/conftest.py по отдельности.
__all__ = ["Setting", "User"]
