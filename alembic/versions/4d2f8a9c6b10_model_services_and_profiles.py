"""model services, model profiles, and conversation model selection

Revision ID: 4d2f8a9c6b10
Revises: f2a91b7c60d4
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d2f8a9c6b10"
down_revision: Union[str, Sequence[str], None] = "f2a91b7c60d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_services",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("credential_ref", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_services_slug", "model_services", ["slug"], unique=True)

    op.create_table(
        "model_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("model_id", sa.String(length=240), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["model_services.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_profiles_service_id", "model_profiles", ["service_id"])
    op.create_index("ix_model_profiles_slug", "model_profiles", ["slug"], unique=True)

    op.add_column(
        "conversations",
        sa.Column("model_profile_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversations_model_profile_id",
        "conversations",
        "model_profiles",
        ["model_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_conversations_model_profile_id", "conversations", ["model_profile_id"])

    op.add_column("messages", sa.Column("model_profile_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_messages_model_profile_id",
        "messages",
        "model_profiles",
        ["model_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_messages_model_profile_id", "messages", ["model_profile_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_model_profile_id", table_name="messages")
    op.drop_constraint("fk_messages_model_profile_id", "messages", type_="foreignkey")
    op.drop_column("messages", "model_profile_id")
    op.drop_index("ix_conversations_model_profile_id", table_name="conversations")
    op.drop_constraint("fk_conversations_model_profile_id", "conversations", type_="foreignkey")
    op.drop_column("conversations", "model_profile_id")
    op.drop_index("ix_model_profiles_slug", table_name="model_profiles")
    op.drop_index("ix_model_profiles_service_id", table_name="model_profiles")
    op.drop_table("model_profiles")
    op.drop_index("ix_model_services_slug", table_name="model_services")
    op.drop_table("model_services")
