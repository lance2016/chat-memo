"""consolidation runs

Revision ID: b0b7e10ab9dd
Revises: 4d2f8a9c6b10
Create Date: 2026-08-08 20:36:05.282837

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b0b7e10ab9dd'
down_revision: Union[str, Sequence[str], None] = '4d2f8a9c6b10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增每日整理的执行记录。

    autogenerate 顺带扫出 model_profiles / model_services 三列的 json → jsonb
    类型差异（模型声明了 JSONB variant，建表时用的是 JSON）。**故意没有合并进来**：
    那是另一件事，混在一起会让这次迁移的意图变模糊，回滚时也说不清在滚什么。
    单独由 c1e4f7a20b83 处理。
    """
    op.create_table('consolidation_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('summarized_conversations', sa.Integer(), nullable=False),
    sa.Column('memory_writes', sa.Integer(), nullable=False),
    sa.Column('index_issues', sa.Integer(), nullable=False),
    sa.Column('seconds', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_consolidation_runs_day'), 'consolidation_runs', ['day'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_consolidation_runs_day'), table_name='consolidation_runs')
    op.drop_table('consolidation_runs')
