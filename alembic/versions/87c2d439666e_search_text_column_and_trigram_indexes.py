"""search text column and trigram indexes

Revision ID: 87c2d439666e
Revises: 70b5a4b50eb5
Create Date: 2026-08-06 01:37:58.158204

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '87c2d439666e'
down_revision: Union[str, Sequence[str], None] = '70b5a4b50eb5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """加搜索列 + 三元组索引。

    GIN 三元组索引对中文 ILIKE 有效（实测 20 万行：cost 4612 → 108，走 Bitmap Index Scan）。
    用子串匹配而不是 tsvector 全文检索，是因为 Postgres 的中文分词要额外装
    zhparser/pg_jieba，镜像里没有；三元组不需要分词，中英文一视同仁。
    """
    op.add_column(
        "messages",
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
    )

    # 回填已有消息：把 content 数组里的 text 块拼起来
    op.execute(
        """
        UPDATE messages SET search_text = COALESCE((
            SELECT string_agg(block->>'text', E'\\n')
            FROM jsonb_array_elements(content) AS block
            WHERE block->>'type' = 'text'
        ), '')
        """
    )

    op.execute(
        "CREATE INDEX ix_messages_search_trgm ON messages "
        "USING gin (search_text gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_memories_content_trgm ON memories "
        "USING gin (content gin_trgm_ops)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_memories_content_trgm")
    op.execute("DROP INDEX IF EXISTS ix_messages_search_trgm")
    op.drop_column("messages", "search_text")
