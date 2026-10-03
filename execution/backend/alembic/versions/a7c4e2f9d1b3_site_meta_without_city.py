"""site meta_without_city: category meta tags without city

Revision ID: a7c4e2f9d1b3
Revises: f3a9c6d2b7e1
Create Date: 2026-10-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c4e2f9d1b3'
down_revision: Union[str, None] = 'f3a9c6d2b7e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sites', sa.Column('meta_without_city', sa.Boolean(), nullable=False,
                                     server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('sites', 'meta_without_city')
