"""conversation thinking override

Revision ID: a1464bcc1e94
Revises: 87c2d439666e
Create Date: 2026-08-06 06:42:42.787194

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1464bcc1e94'
down_revision: Union[str, Sequence[str], None] = '87c2d439666e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """会话级思考开关。NULL 表示跟随全局默认，所以可空且无默认值。"""
    op.add_column("conversations", sa.Column("thinking", sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("conversations", "thinking")
