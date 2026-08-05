"""created_by_id set null on delete

Task 19 (app/api/admin_users.py, delete_user): у article_batches.created_by_id
и job_runs.created_by_id внешние ключи на users.id были объявлены без
ON DELETE, из-за чего Postgres применял NO ACTION по умолчанию. Проверено
вручную на живом Postgres (docker compose up -d postgres, миграции до
головы): создание пользователя, затем ArticleBatch с его created_by_id,
затем `db.delete(user); db.commit()` падает необработанным

    psycopg.errors.ForeignKeyViolation: update or delete on table "users"
    violates foreign key constraint "article_batches_created_by_id_fkey" on
    table "article_batches"
    DETAIL:  Key (id)=(3) is still referenced from table "article_batches".

Партии статей создаются менеджером ежедневно, поэтому это не редкий крайний
случай — delete_user падал бы 500-й почти для любого реально работающего
пользователя панели. Партия/задача — журнал того, что было реально сделано
(тот же принцип и то же обоснование, что и у site_id в этих же моделях,
Task 14) — она должна пережить удаление автора, а не блокировать его
удаление или исчезать вместе с ним. Отсюда SET NULL, а не CASCADE.

Revision ID: 450fdec97dd5
Revises: e25842d72da3
Create Date: 2026-08-05 22:31:23.667592

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '450fdec97dd5'
down_revision: Union[str, None] = 'e25842d72da3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Имена констрейнтов совпадают с теми, что Postgres присвоил автоматически
# при создании таблиц в e25842d72da3 (`<table>_<column>_fkey`) — сохраняем
# их, а не даём autogenerate придумать новые, чтобы downgrade возвращал
# схему бит-в-бит в прежнее состояние, а не в состояние с другим именем
# констрейнта.
ARTICLE_BATCHES_FK = 'article_batches_created_by_id_fkey'
JOB_RUNS_FK = 'job_runs_created_by_id_fkey'


def upgrade() -> None:
    op.alter_column('article_batches', 'created_by_id',
               existing_type=sa.INTEGER(),
               nullable=True)
    op.drop_constraint(ARTICLE_BATCHES_FK, 'article_batches', type_='foreignkey')
    op.create_foreign_key(ARTICLE_BATCHES_FK, 'article_batches', 'users',
                          ['created_by_id'], ['id'], ondelete='SET NULL')
    op.drop_constraint(JOB_RUNS_FK, 'job_runs', type_='foreignkey')
    op.create_foreign_key(JOB_RUNS_FK, 'job_runs', 'users',
                          ['created_by_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(JOB_RUNS_FK, 'job_runs', type_='foreignkey')
    op.create_foreign_key(JOB_RUNS_FK, 'job_runs', 'users', ['created_by_id'], ['id'])
    op.drop_constraint(ARTICLE_BATCHES_FK, 'article_batches', type_='foreignkey')
    op.create_foreign_key(ARTICLE_BATCHES_FK, 'article_batches', 'users',
                          ['created_by_id'], ['id'])
    # Внимание: downgrade не пытается заполнить существовавшие NULL обратно —
    # если к моменту отката какие-то article_batches.created_by_id уже NULL
    # (пользователь был удалён при работавшем SET NULL), alter_column на
    # NOT NULL здесь упадёт. Это осознанно: восстановить исходного автора
    # после SET NULL уже невозможно, откат в этом случае обязан быть громким
    # сбоем миграции, а не молчаливой потерей целостности данных.
    op.alter_column('article_batches', 'created_by_id',
               existing_type=sa.INTEGER(),
               nullable=False)
