"""rename images_regenerating to regenerating

Флаг раньше означал ровно одно: «идёт перегенерация картинок». Теперь под
ним могут идти три разных вида работы (текст/картинки/обложка,
directions/2026-09-09-article-full-regeneration-design.md) — оставлять имя
images_regenerating было бы прямой ложью для двух из трёх кнопок.

Revision ID: b5a9c64497ea
Revises: a2daefb8e7f3
Create Date: 2026-09-09 10:13:35.361410

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5a9c64497ea'
down_revision: Union[str, None] = 'a2daefb8e7f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('articles', 'images_regenerating', new_column_name='regenerating',
                    existing_type=sa.Boolean(), existing_nullable=False,
                    existing_server_default=sa.false())


def downgrade() -> None:
    op.alter_column('articles', 'regenerating', new_column_name='images_regenerating',
                    existing_type=sa.Boolean(), existing_nullable=False,
                    existing_server_default=sa.false())
