"""skills metadata

Revision ID: e5c8a1d73f20
Revises: a7e3c95f1d02
Create Date: 2026-08-09

技能正文在磁盘上，这张表只存来源和启用状态 —— 没有行也是合法状态
（手动拷进技能目录的技能），所以不需要任何数据迁移。
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5c8a1d73f20'
down_revision: Union[str, Sequence[str], None] = 'a7e3c95f1d02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("name", sa.String(length=64), primary_key=True),
        sa.Column("source", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("ref", sa.String(length=120), nullable=False, server_default=""),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("skills")
