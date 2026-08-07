"""timeline reminders and notifications

Revision ID: f2a91b7c60d4
Revises: e91c4d7a2b58
Create Date: 2026-08-07 18:20:14.882301

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f2a91b7c60d4'
down_revision: Union[str, Sequence[str], None] = 'e91c4d7a2b58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 老事项默认都参与提醒；不想被提醒的可以单条关掉。
    op.add_column(
        'timeline_items',
        sa.Column('notify', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column('timeline_items', 'notify', server_default=None)
    # NULL = 按 kind 取默认提前量，不是「提前 0 分钟」。
    op.add_column('timeline_items', sa.Column('lead_minutes', sa.Integer(), nullable=True))
    op.add_column(
        'timeline_items',
        sa.Column('remind_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'timeline_items',
        sa.Column('snoozed_until', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_timeline_items_remind_at', 'timeline_items', ['remind_at'])

    # 存量事项的 remind_at 留空：回填等于给一批过去的事项排上提醒，
    # 首次开启通知时会被补跑式的 ticker 一次性冲出来。让用户改一次时间来激活更安全。

    op.create_table(
        'notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dedupe_key', sa.String(length=160), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('title', sa.String(length=240), nullable=False),
        sa.Column('body', sa.Text(), nullable=False, server_default=''),
        sa.Column('url', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('timeline_item_id', sa.Integer(), nullable=True),
        sa.Column('channels', sa.String(length=120), nullable=False, server_default=''),
        sa.Column('error', sa.Text(), nullable=False, server_default=''),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    # 幂等的全部依据。补跑式 ticker 会反复扫到同一批，靠这个唯一约束挡住重复发送。
    op.create_index('ix_notifications_dedupe_key', 'notifications', ['dedupe_key'], unique=True)
    op.create_index('ix_notifications_created_at', 'notifications', ['created_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_notifications_created_at', table_name='notifications')
    op.drop_index('ix_notifications_dedupe_key', table_name='notifications')
    op.drop_table('notifications')
    op.drop_index('ix_timeline_items_remind_at', table_name='timeline_items')
    op.drop_column('timeline_items', 'snoozed_until')
    op.drop_column('timeline_items', 'remind_at')
    op.drop_column('timeline_items', 'lead_minutes')
    op.drop_column('timeline_items', 'notify')
