"""timeline items

Revision ID: c84e71a50d9f
Revises: b3d7e1a95c42
Create Date: 2026-08-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c84e71a50d9f"
down_revision: Union[str, Sequence[str], None] = "b3d7e1a95c42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "timeline_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("all_day", sa.Boolean(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("location", sa.String(length=240), nullable=False),
        sa.Column("recurrence", sa.String(length=24), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("source_conversation_id", sa.Integer(), nullable=True),
        sa.Column("source_message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_timeline_items_kind"), "timeline_items", ["kind"])
    op.create_index(op.f("ix_timeline_items_status"), "timeline_items", ["status"])
    op.create_index(op.f("ix_timeline_items_starts_at"), "timeline_items", ["starts_at"])
    op.create_index(op.f("ix_timeline_items_source_conversation_id"), "timeline_items", ["source_conversation_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_timeline_items_source_conversation_id"), table_name="timeline_items")
    op.drop_index(op.f("ix_timeline_items_starts_at"), table_name="timeline_items")
    op.drop_index(op.f("ix_timeline_items_status"), table_name="timeline_items")
    op.drop_index(op.f("ix_timeline_items_kind"), table_name="timeline_items")
    op.drop_table("timeline_items")
