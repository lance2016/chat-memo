"""attachments

Revision ID: b3d81f0c67a4
Revises: e5c8a1d73f20
Create Date: 2026-08-09 10:12:44.183920

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b3d81f0c67a4'
down_revision: Union[str, Sequence[str], None] = 'e5c8a1d73f20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'attachments',
        sa.Column('id', sa.Integer(), nullable=False),
        # 两个外键都可空：上传发生在发送之前，那一刻消息和会话都可能还不存在。
        sa.Column('conversation_id', sa.Integer(), nullable=True),
        sa.Column('message_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=16), nullable=False, server_default='image'),
        sa.Column('filename', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('mime', sa.String(length=120), nullable=False, server_default=''),
        sa.Column('bytes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('height', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('vision_description', sa.Text(), nullable=False, server_default=''),
        sa.Column('vision_model', sa.String(length=240), nullable=False, server_default=''),
        sa.Column('vision_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        # 消息是软删除的，所以这里不能 CASCADE —— 撤回一条消息不该带走附件行，
        # 那样就查不到「那张图当时是什么」了。
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_attachments_conversation_id'), 'attachments',
                    ['conversation_id'])
    op.create_index(op.f('ix_attachments_message_id'), 'attachments', ['message_id'])
    op.create_index(op.f('ix_attachments_created_at'), 'attachments', ['created_at'])
    # 磁盘去重和「这张图的描述算过没有」都按摘要查，必须有索引。
    op.create_index(op.f('ix_attachments_sha256'), 'attachments', ['sha256'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_attachments_sha256'), table_name='attachments')
    op.drop_index(op.f('ix_attachments_created_at'), table_name='attachments')
    op.drop_index(op.f('ix_attachments_message_id'), table_name='attachments')
    op.drop_index(op.f('ix_attachments_conversation_id'), table_name='attachments')
    op.drop_table('attachments')
