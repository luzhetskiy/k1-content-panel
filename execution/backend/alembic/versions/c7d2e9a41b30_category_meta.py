"""category meta

Revision ID: c7d2e9a41b30
Revises: 4353fdfa69a2
Create Date: 2026-09-16 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7d2e9a41b30'
down_revision: Union[str, None] = '4353fdfa69a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    # server_default у NOT NULL-колонок — чтобы миграция прошла на уже заведённых сайтах.
    op.add_column('sites', sa.Column('meta_enabled', sa.Boolean(), nullable=False,
                                     server_default=sa.false()))
    op.add_column('sites', sa.Column('city', sa.String(length=200), nullable=False,
                                     server_default=''))
    op.add_column('sites', sa.Column('city_in', sa.String(length=200), nullable=False,
                                     server_default=''))
    op.add_column('sites', sa.Column('wordstat_region_id', sa.Integer(), nullable=True))
    op.add_column('sites', sa.Column('brand', sa.String(length=200), nullable=False,
                                     server_default=''))

    op.create_table('category_meta',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('remote_id', sa.Integer(), nullable=False),
    sa.Column('remote_parent_id', sa.Integer(), nullable=True),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('path', sa.String(length=1000), nullable=False),
    sa.Column('slug', sa.String(length=300), nullable=False),
    sa.Column('url', sa.String(length=255), nullable=False),
    sa.Column('skip_reason', sa.String(length=100), nullable=False),
    sa.Column('seed_source_name', sa.String(length=300), nullable=False),
    sa.Column('seed_phrase', sa.String(length=300), nullable=False),
    sa.Column('form_nominative', sa.String(length=300), nullable=False),
    sa.Column('form_buy', sa.String(length=300), nullable=False),
    sa.Column('form_price', sa.String(length=300), nullable=False),
    sa.Column('candidates_json', JSON_TYPE, nullable=True),
    sa.Column('chosen_form', sa.String(length=20), nullable=False),
    sa.Column('nominative_count', sa.Integer(), nullable=True),
    sa.Column('declined_count', sa.Integer(), nullable=True),
    sa.Column('total_count', sa.Integer(), nullable=True),
    sa.Column('low_demand', sa.Boolean(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('h1', sa.String(length=255), nullable=False),
    sa.Column('meta_description', sa.Text(), nullable=False),
    sa.Column('meta_keywords', sa.Text(), nullable=False),
    sa.Column('ai_keywords', sa.Text(), nullable=False),
    sa.Column('previous_json', JSON_TYPE, nullable=True),
    sa.Column('remote_metatag_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error_text', sa.Text(), nullable=False),
    sa.Column('wait_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'remote_id', name='uq_category_meta_site_remote')
    )
    op.create_index(op.f('ix_category_meta_site_id'), 'category_meta', ['site_id'], unique=False)

    op.create_table('meta_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('job_run_id', sa.Integer(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('total', sa.Integer(), nullable=False),
    sa.Column('error_text', sa.Text(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['job_run_id'], ['job_runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_meta_runs_site_id'), 'meta_runs', ['site_id'], unique=False)

    op.create_table('wordstat_cache',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('phrase', sa.String(length=400), nullable=False),
    sa.Column('region_id', sa.Integer(), nullable=False),
    sa.Column('body', JSON_TYPE, nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kind', 'phrase', 'region_id', name='uq_wordstat_cache_key')
    )

    op.create_table('wordstat_calls',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('called_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_wordstat_calls_called_at'), 'wordstat_calls', ['called_at'],
                    unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_wordstat_calls_called_at'), table_name='wordstat_calls')
    op.drop_table('wordstat_calls')
    op.drop_table('wordstat_cache')
    op.drop_index(op.f('ix_meta_runs_site_id'), table_name='meta_runs')
    op.drop_table('meta_runs')
    op.drop_index(op.f('ix_category_meta_site_id'), table_name='category_meta')
    op.drop_table('category_meta')
    op.drop_column('sites', 'brand')
    op.drop_column('sites', 'wordstat_region_id')
    op.drop_column('sites', 'city_in')
    op.drop_column('sites', 'city')
    op.drop_column('sites', 'meta_enabled')
