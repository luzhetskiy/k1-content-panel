from app.models.article import Article, ArticleBatch, ArticleImage
from app.models.job import JobRun, LlmUsage
from app.models.prompt_template import PromptTemplate
from app.models.setting import Setting
from app.models.site import Site
from app.models.user import User

# Единая точка регистрации моделей: alembic/env.py и tests/conftest.py делают
# `import app.models`, чтобы Base.metadata увидел все таблицы разом. Новую
# модель — добавляй сюда, а не в env.py/conftest.py по отдельности.
__all__ = [
    "Article", "ArticleBatch", "ArticleImage", "JobRun", "LlmUsage",
    "PromptTemplate", "Setting", "Site", "User",
]
