"""add builder reference fields

builder_reference_id — id эталонной карточки строителя на самом сайте (по
образцу reference_article_id у статей); builder_reference_synced_at — когда
последний раз успешно синхронизирован builder_template_html из неё (см.
app/companies/reference.py::sync_builder_reference).

Revision ID: e19ffe68c564
Revises: 95858f3dd890
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e19ffe68c564'
down_revision: Union[str, None] = '95858f3dd890'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sites', sa.Column('builder_reference_id', sa.Integer(), nullable=True))
    op.add_column('sites', sa.Column('builder_reference_synced_at',
                                     sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('sites', 'builder_reference_synced_at')
    op.drop_column('sites', 'builder_reference_id')
