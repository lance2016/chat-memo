"""kb reads

Revision ID: 7c31b2f9d4e8
Revises: 9ab96f778b9c
Create Date: 2026-08-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c31b2f9d4e8'
down_revision: Union[str, Sequence[str], None] = '9ab96f778b9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """知识库（Obsidian vault）访问埋点 —— 将来开写权限的决策依据。"""
    op.create_table(
        "kb_reads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("command", sa.String(length=16), nullable=False),
        sa.Column("target", sa.String(length=512), nullable=False),
        sa.Column("found", sa.Boolean(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_kb_reads_target"), "kb_reads", ["target"])
    op.create_index(op.f("ix_kb_reads_created_at"), "kb_reads", ["created_at"])
    op.create_index("ix_kb_reads_target_created", "kb_reads", ["target", "created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_kb_reads_target_created", table_name="kb_reads")
    op.drop_index(op.f("ix_kb_reads_created_at"), table_name="kb_reads")
    op.drop_index(op.f("ix_kb_reads_target"), table_name="kb_reads")
    op.drop_table("kb_reads")
