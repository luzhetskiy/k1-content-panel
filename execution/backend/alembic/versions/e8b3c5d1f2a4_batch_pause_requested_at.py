"""article batch pause_requested_at

Revision ID: e8b3c5d1f2a4
Revises: d4f1a7c2e8b5
Create Date: 2026-09-18 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8b3c5d1f2a4'
down_revision: Union[str, None] = 'd4f1a7c2e8b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('article_batches',
                  sa.Column('pause_requested_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('article_batches', 'pause_requested_at')
