"""article image regeneration

Кнопка «Перегенерировать картинки» на странице партии (см.
directions/2026-08-09-article-image-regeneration-design.md) заливает новый
раунд content-картинок под versioned-именем файла, не удаляя старые.
`article_images.version` различает раунды одной и той же позиции (1 — то,
что сгенерировано при первой публикации статьи, 2+ — последующие раунды
перегенерации). `articles.images_regenerating` защищает от повторного клика,
пока фоновая Celery-задача не закончила, и служит фронту сигналом для
поллинга статуса партии.

Revision ID: bcba3fe8e22e
Revises: 9864d416847d
Create Date: 2026-08-09 21:13:50.158206

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bcba3fe8e22e'
down_revision: Union[str, None] = '9864d416847d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('article_images',
                  sa.Column('version', sa.Integer(), nullable=False,
                            server_default='1'))
    op.add_column('articles',
                  sa.Column('images_regenerating', sa.Boolean(), nullable=False,
                            server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('articles', 'images_regenerating')
    op.drop_column('article_images', 'version')
