"""model catalog json columns to jsonb

模型声明的是 ``JSON().with_variant(JSONB(), "postgresql")``，但建表那次迁移写的是
纯 ``sa.JSON()`` —— 于是 Postgres 上实际是 ``json``。功能上没差别（SQLAlchemy 两边
都能读写），但每次 ``alembic revision --autogenerate`` 都会把这三列的差异重新扫出来，
混进无关的迁移里。**一个总在报噪音的工具，人会学会忽略它的输出。**

表很小（内置服务两条、档案两条），转换是瞬间的。

Revision ID: c1e4f7a20b83
Revises: b0b7e10ab9dd
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1e4f7a20b83"
down_revision: Union[str, Sequence[str], None] = "b0b7e10ab9dd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("model_profiles", "capabilities"),
    ("model_profiles", "options"),
    ("model_services", "config"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=postgresql.JSON(astext_type=sa.Text()),
            type_=postgresql.JSONB(astext_type=sa.Text()),
            existing_nullable=False,
            postgresql_using=f"{column}::jsonb",
        )


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
            type_=postgresql.JSON(astext_type=sa.Text()),
            existing_nullable=False,
            postgresql_using=f"{column}::json",
        )
