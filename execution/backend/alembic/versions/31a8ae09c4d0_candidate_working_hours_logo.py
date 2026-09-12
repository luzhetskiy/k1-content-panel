"""candidate working hours logo

Revision ID: 31a8ae09c4d0
Revises: b5a9c64497ea
Create Date: 2026-09-12 18:03:48.419389

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '31a8ae09c4d0'
down_revision: Union[str, None] = 'b5a9c64497ea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("company_candidates",
                  sa.Column("working_hours", sa.Text(),
                            nullable=False, server_default=""))
    op.add_column("company_candidates",
                  sa.Column("logo_url", sa.Text(),
                            nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("company_candidates", "logo_url")
    op.drop_column("company_candidates", "working_hours")
