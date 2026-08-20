"""site reference image ratios

Пропорции контентных картинок статьи всегда кадрировались одной и той же
константой CONTENT_CROP="3:2" (app/articles/builder.py), независимо от
реальной вёрстки сайта. На stroybaza-moscow.ru первая картинка статьи
рендерится в блоке .article-hero, рассчитанном на широкий баннер (эталон —
1180×488 ≈ 2.42:1), а не на 3:2 — из-за этого генерируемые обложки статьи
получались заметно выше эталонной.

sync_site_reference (app/sites/reference.py) теперь измеряет реальные
пиксельные размеры каждой картинки эталона и сохраняет их построчно как
"W:H" через запятую в этом новом поле; ArticleBuilder._crop_for_position
берёт готовую пропорцию своей позиции оттуда вместо CONTENT_CROP, если она
измерилась. Пустая строка (default) — старое поведение без изменений,
пока сайт не пересинхронизирован.

Revision ID: a2daefb8e7f3
Revises: e19ffe68c564
Create Date: 2026-08-20 14:51:44.873349

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2daefb8e7f3'
down_revision: Union[str, None] = 'e19ffe68c564'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sites',
                  sa.Column('reference_image_ratios', sa.Text(), nullable=False,
                            server_default=''))


def downgrade() -> None:
    op.drop_column('sites', 'reference_image_ratios')
