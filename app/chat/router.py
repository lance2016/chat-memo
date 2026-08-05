from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.service import ChatService
from app.db.models import Conversation, ConversationSummary, Message
from app.config import get_settings
from app.db.session import get_session, get_sessionmaker
from app.llm.factory import get_provider
from app.memory.prompt import build_system_prompt
from app.memory.store import MemoryStore
from app.memory.tool import MemoryToolExecutor
from app.search import search as run_search
from app.security import require_api_key
from app.timeutils import local_day_bounds

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


class ChatRequest(BaseModel):
    conversation_id: int
    content: str = Field(min_length=1)


class ConversationOut(BaseModel):
    id: int
    title: str
    created_at: Any
    updated_at: Any
    # null = 跟随全局默认（见 GET /api/settings 的 thinking_default）
    thinking: bool | None = None


class SettingsOut(BaseModel):
    provider: str
    model: str
    thinking_default: bool
    # 当前模型支不支持关闭思考。前端据此决定开关是否可用，不要硬编码模型名。
    thinking_toggle: bool


class ConversationUpdate(BaseModel):
    title: str | None = None
    # 传 null 表示恢复成「跟随全局默认」
    thinking: bool | None = None


@router.get("/settings", response_model=SettingsOut)
async def get_runtime_settings() -> SettingsOut:
    """当前生效的模型配置。前端渲染思考开关时先读这个。"""
    settings = get_settings()
    is_anthropic = settings.provider == "anthropic"
    return SettingsOut(
        provider=settings.provider,
        model=settings.model if is_anthropic else settings.deepseek_model,
        thinking_default=True if is_anthropic else settings.deepseek_thinking,
        thinking_toggle=True,
    )


class MessageOut(BaseModel):
    id: int
    role: str
    content: list[dict[str, Any]]
    # 只有 assistant 消息有；字段名随 provider 不同（DeepSeek 是 prompt_tokens，
    # Anthropic 是 input_tokens），前端按需读取。
    usage: dict[str, Any] | None = None
    created_at: Any


class SummaryOut(BaseModel):
    id: int
    conversation_id: int
    conversation_title: str
    summary: str
    created_at: Any


class DailyUsageOut(BaseModel):
    day: str
    messages: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int


class TruncateOut(BaseModel):
    deleted: int


class ConversationHitOut(BaseModel):
    conversation_id: int
    title: str
    message_id: int
    role: str
    snippet: str
    matches: int
    created_at: Any


class MemoryHitOut(BaseModel):
    path: str
    snippet: str


class SearchOut(BaseModel):
    query: str
    conversations: list[ConversationHitOut]
    memories: list[MemoryHitOut]


@router.get("/search", response_model=SearchOut)
async def search_all(
    q: str,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
) -> Any:
    """同时搜对话历史和记忆。查询短于 2 个字符时直接返回空结果。"""
    return await run_search(session, q, limit=limit)


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    conversation = Conversation()
    session.add(conversation)
    await session.flush()
    return conversation


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    limit: int = 50,
    archived: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[Conversation]:
    """默认只返回未归档的；archived=true 返回归档的那批。"""
    condition = (
        Conversation.archived_at.is_not(None)
        if archived
        else Conversation.archived_at.is_(None)
    )
    stmt = (
        select(Conversation)
        .where(condition)
        .order_by(Conversation.updated_at.desc())
        .limit(min(limit, 200))
    )
    return list((await session.execute(stmt)).scalars())


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: int,
    payload: ConversationUpdate,
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    """改标题或该会话的思考开关。

    只更新请求里显式给出的字段 —— ``thinking: null`` 是有意义的值（恢复跟随全局），
    所以用 ``exclude_unset`` 区分「没传」和「传了 null」。
    """
    conversation = await _require_conversation(session, conversation_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(conversation, field, value)
    return conversation


@router.post("/conversations/{conversation_id}/archive", response_model=ConversationOut)
async def archive_conversation(
    conversation_id: int,
    archived: bool = True,
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    """归档/取消归档。archived=false 即恢复。"""
    conversation = await _require_conversation(session, conversation_id)
    conversation.archived_at = dt.datetime.now(dt.UTC) if archived else None
    return conversation


@router.delete(
    "/conversations/{conversation_id}/messages", response_model=TruncateOut
)
async def truncate_messages(
    conversation_id: int,
    after: int = 0,
    session: AsyncSession = Depends(get_session),
) -> TruncateOut:
    """删掉 id 大于 ``after`` 的所有消息，用于「重新生成」和「编辑重发」。

    ``after=0`` 清空整个会话。截断后可能留下没有配对结果的 tool_use，
    但 ``sanitize_history`` 在加载历史时会补齐，不用担心把会话截坏。
    """
    await _require_conversation(session, conversation_id)
    result = await session.execute(
        sa_delete(Message).where(
            Message.conversation_id == conversation_id, Message.id > after
        )
    )
    deleted = result.rowcount or 0
    if deleted:
        logger.info("✁ conv#%s 截断 %d 条消息 (after=%s)", conversation_id, deleted, after)
    return TruncateOut(deleted=deleted)


@router.get("/summaries", response_model=list[SummaryOut])
async def list_summaries(
    day: dt.date | None = None,
    conversation_id: int | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> list[SummaryOut]:
    """每日整理生成的会话摘要。不传 day 和 conversation_id 就返回最近的。"""
    stmt = (
        select(ConversationSummary, Conversation.title)
        .join(Conversation, Conversation.id == ConversationSummary.conversation_id)
        .order_by(ConversationSummary.created_at.desc())
        .limit(min(limit, 200))
    )
    if day is not None:
        start, end = local_day_bounds(day)
        stmt = stmt.where(
            ConversationSummary.created_at >= start,
            ConversationSummary.created_at < end,
        )
    if conversation_id is not None:
        stmt = stmt.where(ConversationSummary.conversation_id == conversation_id)

    return [
        SummaryOut(
            id=row.id,
            conversation_id=row.conversation_id,
            conversation_title=title,
            summary=row.summary,
            created_at=row.created_at,
        )
        for row, title in (await session.execute(stmt)).all()
    ]


@router.get("/usage", response_model=list[DailyUsageOut])
async def daily_usage(
    days: int = 7,
    session: AsyncSession = Depends(get_session),
) -> list[DailyUsageOut]:
    """按天汇总 token 用量。字段名各家不同，这里统一归一化。"""
    today = dt.date.today()
    out: list[DailyUsageOut] = []
    for offset in range(min(days, 90)):
        day = today - dt.timedelta(days=offset)
        start, end = local_day_bounds(day)
        stmt = select(Message.usage).where(
            Message.usage.is_not(None),
            Message.created_at >= start,
            Message.created_at < end,
        )
        rows = [u for u in (await session.execute(stmt)).scalars() if u]
        out.append(
            DailyUsageOut(
                day=day.isoformat(),
                messages=len(rows),
                input_tokens=sum(_pick(u, "input_tokens", "prompt_tokens") for u in rows),
                output_tokens=sum(
                    _pick(u, "output_tokens", "completion_tokens") for u in rows
                ),
                cached_tokens=sum(
                    _pick(u, "cache_read_input_tokens", "prompt_cache_hit_tokens")
                    for u in rows
                ),
            )
        )
    return out


def _pick(usage: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int):
            return value
    return 0


async def _require_conversation(
    session: AsyncSession, conversation_id: int
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    return conversation


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[Message]:
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    )
    return list((await session.execute(stmt)).scalars())


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    await session.delete(await _require_conversation(session, conversation_id))


@router.post("/chat")
async def chat(payload: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _stream(payload),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream(payload: ChatRequest) -> AsyncIterator[str]:
    """SSE 生成器自己持有数据库会话。

    不能用 Depends(get_session)：请求处理函数返回后依赖就会被清理，而生成器此时才刚开始跑。
    """
    async with get_sessionmaker()() as session:
        try:
            conversation = await session.get(Conversation, payload.conversation_id)
            if conversation is None:
                yield _sse({"type": "error", "message": "会话不存在"})
                return

            store = MemoryStore(session, actor="chat", conversation_id=conversation.id)
            service = ChatService(
                session=session,
                provider=get_provider(),
                executor=MemoryToolExecutor(store),
            )
            system = await build_system_prompt(store)

            async for event in service.stream_reply(
                conversation=conversation, system=system, user_text=payload.content
            ):
                yield _sse(_to_payload(event))
        except Exception as exc:
            logger.exception("对话流处理失败")
            await session.rollback()
            yield _sse({"type": "error", "message": f"服务端错误：{exc}"})


def _to_payload(event: Any) -> dict[str, Any]:
    if isinstance(event, tuple):
        name, data = event
        return {"type": name, **data}
    if is_dataclass(event) and not isinstance(event, type):
        return asdict(event)
    return {"type": "error", "message": f"未知事件 {event!r}"}


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
