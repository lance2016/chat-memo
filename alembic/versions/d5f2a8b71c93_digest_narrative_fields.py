"""digest narrative fields

Revision ID: d5f2a8b71c93
Revises: c84e71a50d9f
Create Date: 2026-08-07 11:04:52.117903

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd5f2a8b71c93'
down_revision: Union[str, Sequence[str], None] = 'c84e71a50d9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    # 老 digest 没有这四样，给默认值而不是 NULL —— 前端把「空字符串」当没有，
    # 少一层 null 判断。server_default 建完就撤，让模型层的 default 说了算。
    op.add_column('daily_digests', sa.Column('title', sa.Text(), nullable=False, server_default=''))
    op.add_column('daily_digests', sa.Column('observation', sa.Text(), nullable=False, server_default=''))
    op.add_column('daily_digests', sa.Column('quote', sa.Text(), nullable=False, server_default=''))
    op.add_column('daily_digests', sa.Column('echoes', _JSON, nullable=False, server_default='[]'))
    for column in ('title', 'observation', 'quote', 'echoes'):
        op.alter_column('daily_digests', column, server_default=None)

    op.add_column('conversation_summaries', sa.Column('quote', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('conversation_summaries', 'quote')
    for column in ('echoes', 'quote', 'observation', 'title'):
        op.drop_column('daily_digests', column)
