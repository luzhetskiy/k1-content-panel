"""drop unused site-level teaser ids

teaser_category_id/teaser_city_id/teaser_location_id на Site никогда не
читались: реальный источник — одноимённые колонки на CompanyBatch
(app/companies/builder.py читает batch.teaser_*, не site.teaser_*), так как
эти ID у одной партии могут отличаться от другой (см. форму создания партии).
Site-уровневые дублировали их без единого потребителя — чистый мёртвый код.

Revision ID: 95858f3dd890
Revises: bcba3fe8e22e
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '95858f3dd890'
down_revision: Union[str, None] = 'bcba3fe8e22e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('sites', 'teaser_category_id')
    op.drop_column('sites', 'teaser_city_id')
    op.drop_column('sites', 'teaser_location_id')


def downgrade() -> None:
    op.add_column('sites', sa.Column('teaser_location_id', sa.Integer(), nullable=True))
    op.add_column('sites', sa.Column('teaser_city_id', sa.Integer(), nullable=True))
    op.add_column('sites', sa.Column('teaser_category_id', sa.Integer(), nullable=True))
