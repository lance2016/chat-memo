"""daily digests and open loops

Revision ID: b3d7e1a95c42
Revises: 7c31b2f9d4e8
Create Date: 2026-08-07 10:12:03.884210

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3d7e1a95c42'
down_revision: Union[str, Sequence[str], None] = '7c31b2f9d4e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'daily_digests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('headline', sa.Text(), nullable=False),
        sa.Column(
            'highlights',
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
            nullable=False,
        ),
        sa.Column('model', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_daily_digests_day'), 'daily_digests', ['day'], unique=True)

    op.create_table(
        'open_loops',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('opened_on', sa.Date(), nullable=False),
        sa.Column('closed_on', sa.Date(), nullable=True),
        sa.Column('closed_note', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('source_conversation_id', sa.Integer(), nullable=True),
        sa.Column('actor', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_open_loops_opened_on'), 'open_loops', ['opened_on'], unique=False)
    op.create_index(op.f('ix_open_loops_closed_on'), 'open_loops', ['closed_on'], unique=False)

    # 老摘要没有这一份，保持 NULL —— 回顾页把「没有 recap」当正常态。
    op.add_column('conversation_summaries', sa.Column('recap', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('conversation_summaries', 'recap')
    op.drop_index(op.f('ix_open_loops_closed_on'), table_name='open_loops')
    op.drop_index(op.f('ix_open_loops_opened_on'), table_name='open_loops')
    op.drop_table('open_loops')
    op.drop_index(op.f('ix_daily_digests_day'), table_name='daily_digests')
    op.drop_table('daily_digests')
