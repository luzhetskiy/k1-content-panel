"""company import stored file and new count

Revision ID: d4f1a7c2e8b5
Revises: c7d2e9a41b30
Create Date: 2026-09-18 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f1a7c2e8b5'
down_revision: Union[str, None] = 'c7d2e9a41b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # new_count без server_default: у старых загрузок число новых неизвестно (NULL).
    op.add_column('company_imports', sa.Column('new_count', sa.Integer(), nullable=True))
    op.add_column('company_imports', sa.Column('stored_path', sa.String(length=500),
                                               nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('company_imports', 'stored_path')
    op.drop_column('company_imports', 'new_count')
