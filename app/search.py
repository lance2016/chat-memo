"""全局搜索：对话历史 + 记忆。

用 ``ILIKE '%q%'`` 子串匹配，不用 tsvector 全文检索 —— Postgres 的中文分词需要额外装
zhparser/pg_jieba，镜像里没有。三元组索引让子串匹配在中文上一样快（实测 20 万行
cost 4612 → 108），而且中英文、代码片段一视同仁，不需要分词。

代价是不做词干还原和相关度打分：搜「运行」不会命中「跑」。对个人助手够用。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, Memory, Message

SNIPPET_RADIUS = 40
MIN_QUERY_LENGTH = 2


@dataclass
class ConversationHit:
    conversation_id: int
    title: str
    message_id: int
    role: str
    snippet: str
    matches: int
    created_at: object


@dataclass
class MemoryHit:
    path: str
    snippet: str


@dataclass
class SearchResults:
    query: str
    conversations: list[ConversationHit] = field(default_factory=list)
    memories: list[MemoryHit] = field(default_factory=list)


async def search(session: AsyncSession, query: str, limit: int = 20) -> SearchResults:
    q = query.strip()
    # 单字查询会命中几乎所有内容，且三元组索引对 1 字符退化成全表扫。
    if len(q) < MIN_QUERY_LENGTH:
        return SearchResults(query=q)

    pattern = f"%{_escape(q)}%"
    return SearchResults(
        query=q,
        conversations=await _search_conversations(session, q, pattern, limit),
        memories=await _search_memories(session, q, pattern, limit),
    )


async def _search_conversations(
    session: AsyncSession, q: str, pattern: str, limit: int
) -> list[ConversationHit]:
    """按会话聚合，而不是每条消息一行。

    一个会话里命中 5 条消息，用户想看到的是「这个会话相关」，不是 5 条重复条目。
    """
    stmt = (
        select(Message, Conversation.title)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.search_text.ilike(pattern, escape="\\"))
        .order_by(Message.created_at.desc())
        # 多取一些，聚合后才够 limit 个会话
        .limit(min(limit, 50) * 5)
    )

    hits: dict[int, ConversationHit] = {}
    for message, title in (await session.execute(stmt)).all():
        existing = hits.get(message.conversation_id)
        if existing is not None:
            # 同一会话的后续命中只累加计数，摘要用最新那条
            existing.matches += 1
            continue
        hits[message.conversation_id] = ConversationHit(
            conversation_id=message.conversation_id,
            title=title,
            message_id=message.id,
            role=message.role,
            snippet=make_snippet(message.search_text, q),
            matches=1,
            created_at=message.created_at,
        )
    # 不在循环里提前截断：要把所有取到的行都记进 matches，计数才准
    return list(hits.values())[:limit]


async def _search_memories(
    session: AsyncSession, q: str, pattern: str, limit: int
) -> list[MemoryHit]:
    stmt = (
        select(Memory)
        .where(Memory.content.ilike(pattern, escape="\\"))
        .order_by(Memory.path)
        .limit(min(limit, 50))
    )
    return [
        MemoryHit(path=m.path, snippet=make_snippet(m.content, q))
        for m in (await session.execute(stmt)).scalars()
    ]


def make_snippet(text: str, query: str, radius: int = SNIPPET_RADIUS) -> str:
    """截取命中位置前后一段。前端自己在片段里高亮，不需要后端标记位置。"""
    flat = " ".join(text.split())
    index = flat.lower().find(query.lower())
    if index < 0:
        return flat[: radius * 2] + ("…" if len(flat) > radius * 2 else "")

    start = max(index - radius, 0)
    end = min(index + len(query) + radius, len(flat))
    return (
        ("…" if start > 0 else "")
        + flat[start:end]
        + ("…" if end < len(flat) else "")
    )


def _escape(q: str) -> str:
    """转义 LIKE 通配符，否则用户搜 `%` 会match 到所有内容。"""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
