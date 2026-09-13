"""batch run_requested_at

Revision ID: 4353fdfa69a2
Revises: 31a8ae09c4d0
Create Date: 2026-09-13 14:23:28.515252

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4353fdfa69a2'
down_revision: Union[str, None] = '31a8ae09c4d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # nullable без server_default: у партий, созданных до этой миграции, момент
    # запуска неизвестен, и NULL говорит об этом честно. Подставлять им created_at
    # было бы выдумкой — batch_runtime_state (app/api/article_batches.py) на NULL
    # просто не делает вывода «ждёт в очереди», что для уже завершённых партий
    # ровно то, что нужно.
    op.add_column("article_batches",
                  sa.Column("run_requested_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("article_batches", "run_requested_at")
