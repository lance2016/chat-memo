"""message soft delete

Revision ID: a7e3c95f1d02
Revises: c1e4f7a20b83
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7e3c95f1d02'
down_revision: Union[str, Sequence[str], None] = 'c1e4f7a20b83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """编辑重发/重新生成改成软删除。

    存量数据全是「没被撤下」，NULL 即可，不需要回填。索引挂 (conversation_id,
    deleted_at)：读历史每次都要按会话取未删除的消息，这是最热的那条路径。
    """
    op.add_column(
        "messages", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_messages_conversation_live",
        "messages",
        ["conversation_id", "deleted_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_messages_conversation_live", table_name="messages")
    op.drop_column("messages", "deleted_at")
