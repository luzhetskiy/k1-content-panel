"""category meta seo_text, product names, run mode

Revision ID: f3a9c6d2b7e1
Revises: e8b3c5d1f2a4
Create Date: 2026-09-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f3a9c6d2b7e1'
down_revision: Union[str, None] = 'e8b3c5d1f2a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('category_meta',
                  sa.Column('seo_text', sa.Text(), nullable=False, server_default=''))
    op.add_column('category_meta', sa.Column('product_names_json', JSON_TYPE, nullable=True))
    op.add_column('meta_runs',
                  sa.Column('mode', sa.String(length=20), nullable=False, server_default='tags'))


def downgrade() -> None:
    op.drop_column('meta_runs', 'mode')
    op.drop_column('category_meta', 'product_names_json')
    op.drop_column('category_meta', 'seo_text')
