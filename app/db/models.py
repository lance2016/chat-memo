from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    # JSONB 在 Postgres 上用原生类型，在 SQLite（测试）上退化为 JSON。
    type_annotation_map = {dict[str, Any]: JSON().with_variant(JSONB(), "postgresql")}


def _now_column() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 该会话是否思考。NULL = 跟随全局默认，前端切换时才写具体值。
    thinking: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    """一轮完整的 Anthropic 消息。

    ``content`` 存 content block 数组原文（含 thinking / tool_use / tool_result），
    多轮回传时必须原样送回 —— 只抽 text 会破坏 thinking 签名并触发 400。
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    usage: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    # 只含 text 块的纯文本，写入时冗余出来专供搜索。
    # 正文埋在 JSONB 数组里没法直接建索引；抽出来才能挂 GIN 三元组索引，
    # 也顺带把 thinking / tool_use 这些噪音排除在搜索结果之外。
    search_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    summary: Mapped[str] = mapped_column(Text)
    # 摘要覆盖到的最后一条消息，增量生成时作为水位线。
    up_to_message_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Memory(Base):
    """一条长期记忆，逻辑上是记忆文件树里的一个文件。"""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MemoryVersion(Base):
    """每次记忆变更的不可变快照，用于审计和回滚。

    删除后原 memory 行消失，所以这里不设外键，只留 path 和 memory_id 值。
    """

    __tablename__ = "memory_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    path: Mapped[str] = mapped_column(String(512), index=True)
    content: Mapped[str] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(String(16))  # created|modified|deleted
    actor: Mapped[str] = mapped_column(String(16))  # chat|consolidation|manual
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class MemoryRead(Base):
    """模型每次读记忆的埋点。

    写入侧已经有 ``memory_versions`` 了，这里只记**读取**——没有它就无法回答
    「我攒的这些记忆到底有没有被用上」，而那是判断记忆质量的核心指标。

    ``found=False`` 表示模型想读一个不存在的路径，说明索引和实际内容对不上，
    是索引质量的信号。
    """

    __tablename__ = "memory_reads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(512), index=True)
    actor: Mapped[str] = mapped_column(String(16))  # chat|consolidation|ingest|manual
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    found: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


Index("ix_memory_versions_path_created", MemoryVersion.path, MemoryVersion.created_at)
Index("ix_memory_reads_path_created", MemoryRead.path, MemoryRead.created_at)
