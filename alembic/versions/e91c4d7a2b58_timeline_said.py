"""timeline said

Revision ID: e91c4d7a2b58
Revises: d5f2a8b71c93
Create Date: 2026-08-07 11:41:26.540118

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e91c4d7a2b58'
down_revision: Union[str, Sequence[str], None] = 'd5f2a8b71c93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 老事项没有依据可追溯，留空字符串而不是 NULL —— 少一层判空。
    op.add_column(
        'timeline_items',
        sa.Column('said', sa.String(length=120), nullable=False, server_default=''),
    )
    op.alter_column('timeline_items', 'said', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('timeline_items', 'said')
