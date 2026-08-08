"""widen company_candidate phone column

Revision ID: 9864d416847d
Revises: a1c8f0d93b7e
Create Date: 2026-08-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9864d416847d'
down_revision: Union[str, None] = 'a1c8f0d93b7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Реальная выгрузка Яндекс.Карт иногда кладёт в ячейку телефона мусор/рекламный
# текст источника или несколько номеров через запятую — значения намного длиннее
# обычного телефона. String(50) на company_candidates.phone уронил весь батч
# импорта (5214 строк) с psycopg.errors.StringDataRightTruncation на реальном
# инциденте: одно превышающее лимит значение откатывает всю транзакцию commit.
def upgrade() -> None:
    op.alter_column('company_candidates', 'phone',
                    existing_type=sa.String(length=50),
                    type_=sa.String(length=500))


def downgrade() -> None:
    op.alter_column('company_candidates', 'phone',
                    existing_type=sa.String(length=500),
                    type_=sa.String(length=50))
