"""house projects: pages of the section and rewrite runs

Revision ID: b5e1d8c3a9f2
Revises: a7c4e2f9d1b3
Create Date: 2026-10-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5e1d8c3a9f2'
down_revision: Union[str, None] = 'a7c4e2f9d1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sites', sa.Column('projects_enabled', sa.Boolean(), nullable=False,
                                     server_default=sa.false()))
    op.add_column('sites', sa.Column('projects_parent_id', sa.Integer(), nullable=True))
    op.add_column('sites', sa.Column('projects_parent_title', sa.String(length=300),
                                     nullable=False, server_default=''))
    op.create_table(
        'house_project_pages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=False),
        sa.Column('remote_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('url', sa.String(length=500), nullable=False),
        sa.Column('original_html', sa.Text(), nullable=False),
        sa.Column('rewritten_html', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('skip_reason', sa.String(length=100), nullable=False),
        sa.Column('error_text', sa.Text(), nullable=False),
        sa.Column('rewritten_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rewrite_count', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('site_id', 'remote_id', name='uq_house_project_pages_site_remote'),
    )
    op.create_index(op.f('ix_house_project_pages_site_id'), 'house_project_pages', ['site_id'],
                    unique=False)
    op.create_table(
        'project_rewrite_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=False),
        sa.Column('job_run_id', sa.Integer(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('error_text', sa.Text(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['job_run_id'], ['job_runs.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_project_rewrite_runs_site_id'), 'project_rewrite_runs', ['site_id'],
                    unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_project_rewrite_runs_site_id'), table_name='project_rewrite_runs')
    op.drop_table('project_rewrite_runs')
    op.drop_index(op.f('ix_house_project_pages_site_id'), table_name='house_project_pages')
    op.drop_table('house_project_pages')
    op.drop_column('sites', 'projects_parent_title')
    op.drop_column('sites', 'projects_parent_id')
    op.drop_column('sites', 'projects_enabled')
