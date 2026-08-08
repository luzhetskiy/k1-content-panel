"""companies

Revision ID: a1c8f0d93b7e
Revises: 450fdec97dd5
Create Date: 2026-08-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1c8f0d93b7e'
down_revision: Union[str, None] = '450fdec97dd5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('company_imports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=300), nullable=False),
        sa.Column('uploaded_by_id', sa.Integer(), nullable=True),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('matched_count', sa.Integer(), nullable=False),
        sa.Column('error_count', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['uploaded_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table('company_candidates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_key', sa.String(length=300), nullable=False),
        sa.Column('website_raw', sa.String(length=500), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('region_raw', sa.String(length=200), nullable=False),
        sa.Column('category_raw', sa.String(length=300), nullable=False),
        sa.Column('city', sa.String(length=200), nullable=False),
        sa.Column('address', sa.String(length=500), nullable=False),
        sa.Column('phone', sa.String(length=50), nullable=False),
        sa.Column('email', sa.String(length=200), nullable=False),
        sa.Column('rating', sa.Float(), nullable=True),
        sa.Column('reviews_count', sa.Integer(), nullable=False),
        sa.Column('ratings_count', sa.Integer(), nullable=False),
        sa.Column('lat', sa.Float(), nullable=True),
        sa.Column('lon', sa.Float(), nullable=True),
        sa.Column('yandex_url', sa.String(length=500), nullable=False),
        sa.Column('raw_row_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('site_key'),
    )
    op.create_table('company_batches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=True),
        sa.Column('region_raw', sa.String(length=200), nullable=False),
        sa.Column('category_raw', sa.String(length=300), nullable=False),
        sa.Column('category_normalized', sa.String(length=300), nullable=False),
        sa.Column('teaser_category_id', sa.Integer(), nullable=True),
        sa.Column('teaser_city_id', sa.Integer(), nullable=True),
        sa.Column('teaser_location_id', sa.Integer(), nullable=True),
        sa.Column('requested_count', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('error_text', sa.Text(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table('companies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=True),
        sa.Column('batch_id', sa.Integer(), nullable=True),
        sa.Column('candidate_id', sa.Integer(), nullable=True),
        sa.Column('site_key', sa.String(length=300), nullable=False),
        sa.Column('website', sa.String(length=500), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('region', sa.String(length=200), nullable=False),
        sa.Column('category_normalized', sa.String(length=300), nullable=False),
        sa.Column('rating', sa.Float(), nullable=True),
        sa.Column('reviews_count', sa.Integer(), nullable=False),
        sa.Column('yandex_url', sa.String(length=500), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('error_text', sa.Text(), nullable=False),
        sa.Column('remote_page_id', sa.Integer(), nullable=True),
        sa.Column('remote_url', sa.String(length=500), nullable=False),
        sa.Column('teaser_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['company_batches.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['candidate_id'], ['company_candidates.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('site_id', 'site_key', name='uq_company_site_site_key'),
    )
    op.create_index(op.f('ix_companies_batch_id'), 'companies', ['batch_id'], unique=False)
    op.create_table('company_info',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('builder_name', sa.String(length=300), nullable=False),
        sa.Column('city_name', sa.String(length=200), nullable=False),
        sa.Column('city_prepositional', sa.String(length=200), nullable=False),
        sa.Column('builder_logo_src', sa.String(length=500), nullable=False),
        sa.Column('builder_logo_alt', sa.String(length=300), nullable=False),
        sa.Column('about_company', sa.Text(), nullable=False),
        sa.Column('specialization', sa.Text(), nullable=False),
        sa.Column('projects_services', sa.Text(), nullable=False),
        sa.Column('benefits', sa.Text(), nullable=False),
        sa.Column('contacts', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('address', sa.String(length=500), nullable=False),
        sa.Column('coordinates', sa.String(length=100), nullable=False),
        sa.Column('scraped_text', sa.Text(), nullable=False),
        sa.Column('scraped_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id'),
    )


def downgrade() -> None:
    op.drop_table('company_info')
    op.drop_index(op.f('ix_companies_batch_id'), table_name='companies')
    op.drop_table('companies')
    op.drop_table('company_batches')
    op.drop_table('company_candidates')
    op.drop_table('company_imports')
