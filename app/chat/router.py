from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete as sa_delete
from sqlalchemy import or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import build_agent_context
from app.attachments.hydrate import AttachmentHydrator
from app.chat.service import (
    ChatService,
    context_breakdown,
    history_window_stats,
    sanitize_history,
    trim_history,
)
from app.config import get_settings
from app.db.models import (
    Attachment,
    Conversation,
    ConversationSummary,
    Message,
    live_message,
)
from app.db.session import get_session, get_sessionmaker
from app.llm.catalog import resolve_model_target, resolve_vision_target
from app.llm.target import ModelTarget
from app.llm.title import get_title_client
from app.obs import current_trace_id
from app.obs.tracing import apply_tracing
from app.search import search as run_search
from app.security import require_api_key
from app.settings_store import (
    SettingError,
    apply,
    describe,
    is_configured,
    load_overrides,
    resolve_settings,
)
from app.timeutils import local_day_bounds

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


class ChatRequest(BaseModel):
    conversation_id: int | None = None
    # 不再要求 min_length=1：只贴一张图不配文字是正常用法。
    # 「不能两边都空」交给下面的校验器 —— 那才是真正的约束。
    content: str = ""
    # 选择器传入的模型。保存到会话后，后续消息默认继续使用它。
    model_profile_id: int | None = None
    # 只影响这一轮；默认关闭，避免模型在用户没有明确要求时访问外网。
    web_search: bool = False
    # 先上传拿到 id，发送时才挂到消息上。
    attachment_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _not_empty(self) -> ChatRequest:
        if not self.content.strip() and not self.attachment_ids:
            raise ValueError("消息不能为空")
        return self


class ConversationOut(BaseModel):
    id: int
    title: str
    created_at: Any
    updated_at: Any
    # null = 跟随全局默认（见 GET /api/settings 的 thinking_default）
    thinking: bool | None = None
    # null = 第一轮尚未固定模型，或历史数据尚未迁移
    model_profile_id: int | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    # 传 null 表示恢复成「跟随全局默认」
    thinking: bool | None = None
    # null 表示恢复跟随全局默认；聊天请求会在第一轮实际使用后固定档案。
    model_profile_id: int | None = None


@router.get("/settings")
async def get_runtime_settings(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """设置页要的全部信息：当前值、每项来自哪层、可选项。

    ``values`` 是**合并后的生效值**（数据库覆盖叠加在代码默认和基础环境配置之上）。
    """
    settings = await resolve_settings(session)
    overrides = await load_overrides(session)
    try:
        target = await resolve_model_target(session, settings)
    except ValueError:
        # 目录没配好也要能打开设置页 —— 退回旧配置推出来的目标，不在这里重复厂商判断。
        target = ModelTarget.from_settings(settings)
    return {
        **describe(settings, overrides),
        # 兼容前端已有的运行时卡片
        "provider": target.service_slug,
        "model": target.model_id,
        "thinking_default": target.thinking_default,
        "thinking_toggle": True,
        # 知识库（Obsidian vault）是否挂载。只读状态位 —— vault_path 是 .env 配置，设置页改不了
        "kb_enabled": bool(settings.vault_path),
        # 只返回能力状态，不返回 Tavily Key 本身。
        "web_search_enabled": is_configured(settings.tavily_api_key),
        # 能不能贴图。两条路任意一条通就行：聊天模型自己能看图，
        # 或者配了视觉模型档案来做转描述。前端拿它决定上传入口是否可用。
        "vision_enabled": target.supports_vision
        or settings.vision_model_profile_id is not None,
    }


@router.patch("/settings")
async def update_runtime_settings(
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """部分更新。传 ``null`` 表示删掉该覆盖、恢复代码/环境基础默认。

    改完立刻生效，不需要重启 —— 每个请求都会重新解析一次配置。
    """
    base = get_settings()
    # 兼容旧设置页：用户直接改 provider/model 时，清掉新目录的默认引用，
    # 避免界面看起来已经切换，实际聊天仍沿用旧的 profile。
    payload = dict(payload)
    if any(key in payload for key in ("provider", "model", "deepseek_model")):
        payload.setdefault("chat_model_profile_id", None)
    if "consolidate_model" in payload:
        payload.setdefault("consolidate_model_profile_id", None)
    try:
        await apply(session, payload, await resolve_settings(session, base))
    except SettingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await session.flush()
    settings = await resolve_settings(session, base)
    overrides = await load_overrides(session)
    # tracing 的开关在设置页里，改完必须当场生效 —— 否则又变成「点了没反应」。
    # 幂等，配置没变时什么都不做。
    apply_tracing(settings)
    logger.info("⚙ 设置已更新: %s", ", ".join(payload) or "(空)")
    return describe(settings, overrides)


class MessageOut(BaseModel):
    id: int
    role: str
    content: list[dict[str, Any]]
    # 只有 assistant 消息有；字段名随 provider 不同（DeepSeek 是 prompt_tokens，
    # Anthropic 是 input_tokens），前端按需读取。
    usage: dict[str, Any] | None = None
    model_profile_id: int | None = None
    created_at: Any


@router.get("/conversations/{conversation_id}/context")
async def get_conversation_context(
    conversation_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    settings = await resolve_settings(session)
    rows = list(
        (await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id, live_message())
            .order_by(Message.id)
        )).scalars()
    )
    messages = [{"role": row.role, "content": row.content} for row in rows]
    stats = history_window_stats(messages, settings.history_max_chars)
    retained = sanitize_history(trim_history(messages, settings.history_max_chars))
    last_usage = next((row.usage for row in reversed(rows) if row.usage), {}) or {}
    exact_prompt_tokens = int(
        last_usage.get("prompt_tokens", last_usage.get("input_tokens", 0)) or 0
    )
    cached_tokens = int(
        last_usage.get("prompt_cache_hit_tokens", last_usage.get("cache_read_input_tokens", 0)) or 0
    )
    try:
        target = await resolve_model_target(
            session, settings, profile_id=conversation.model_profile_id
        )
    except ValueError:
        # The context meter should still be usable while a model credential is
        # being fixed. The legacy target carries the current model metadata but
        # does not invent a context-window size.
        target = ModelTarget.from_settings(settings)

    try:
        agent_context = await build_agent_context(
            session,
            settings=settings,
            target=target,
            purpose="chat",
            conversation_id=conversation_id,
        )
        system = agent_context.system
        definitions = (
            agent_context.executor.anthropic_definitions
            if target.protocol == "anthropic"
            else agent_context.executor.openai_definitions
        )
        toolkits = list(agent_context.toolkits)
    except Exception:
        # A diagnostics panel must not make an otherwise usable conversation
        # fail because an optional toolkit is temporarily unavailable.
        logger.exception("组装上下文用量面板失败")
        system = ""
        definitions = []
        toolkits = []

    breakdown = context_breakdown(system, definitions, retained)
    estimated_prompt_tokens = sum(int(item.get("tokens", 0)) for item in breakdown)
    if exact_prompt_tokens and estimated_prompt_tokens:
        # The provider gives an exact aggregate, while the category tokenizer
        # is only approximate. Scale categories to that aggregate so the UI
        # never presents a breakdown whose sum is larger than its headline.
        scaled_total = 0
        for item in breakdown[:-1]:
            scaled = round(int(item.get("tokens", 0)) * exact_prompt_tokens / estimated_prompt_tokens)
            item["tokens"] = scaled
            scaled_total += scaled
        if breakdown:
            breakdown[-1]["tokens"] = max(0, exact_prompt_tokens - scaled_total)
    prompt_tokens = exact_prompt_tokens or estimated_prompt_tokens
    window = target.context_window_tokens
    return {
        **stats,
        "prompt_tokens": prompt_tokens,
        "exact_prompt_tokens": exact_prompt_tokens,
        "estimated_prompt_tokens": estimated_prompt_tokens,
        "cached_tokens": cached_tokens,
        "estimated": exact_prompt_tokens == 0,
        "model_id": target.model_id,
        "model_name": target.display_name,
        "context_window_tokens": window,
        "max_output_tokens": target.max_tokens,
        "remaining_tokens": max(window - prompt_tokens, 0) if window else None,
        "used_percent": round(prompt_tokens / window * 100, 1) if window else None,
        "breakdown": breakdown,
        "toolkits": toolkits,
    }


@router.get("/context")
async def get_context_preview(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Context baseline for the empty composer.

    A new conversation has no messages yet, but system prompt and tool schemas
    still consume input space. Showing that baseline keeps the context control
    discoverable on the home screen instead of only after the first message.
    """
    settings = await resolve_settings(session)
    try:
        target = await resolve_model_target(session, settings)
    except ValueError:
        target = ModelTarget.from_settings(settings)
    try:
        agent_context = await build_agent_context(
            session, settings=settings, target=target, purpose="chat"
        )
        system = agent_context.system
        definitions = (
            agent_context.executor.anthropic_definitions
            if target.protocol == "anthropic"
            else agent_context.executor.openai_definitions
        )
        toolkits = list(agent_context.toolkits)
    except Exception:
        logger.exception("组装空会话上下文用量面板失败")
        system, definitions, toolkits = "", [], []
    breakdown = context_breakdown(system, definitions, [])
    estimated = sum(int(item.get("tokens", 0)) for item in breakdown)
    window = target.context_window_tokens
    return {
        "history_chars": 0,
        "history_budget_chars": settings.history_max_chars,
        "retained_messages": 0,
        "retained_turns": 0,
        "trimmed_messages": 0,
        "prompt_tokens": estimated,
        "exact_prompt_tokens": 0,
        "estimated_prompt_tokens": estimated,
        "cached_tokens": 0,
        "estimated": True,
        "model_id": target.model_id,
        "model_name": target.display_name,
        "context_window_tokens": window,
        "max_output_tokens": target.max_tokens,
        "remaining_tokens": max(window - estimated, 0) if window else None,
        "used_percent": round(estimated / window * 100, 1) if window else None,
        "breakdown": breakdown,
        "toolkits": toolkits,
    }


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


class ConversationClearOut(BaseModel):
    deleted_conversations: int
    deleted_messages: int
    deleted_summaries: int


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
    offset: int = 0,
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
        .offset(max(offset, 0))
        .limit(min(limit, 200))
    )
    return list((await session.execute(stmt)).scalars())


@router.delete("/conversations", response_model=ConversationClearOut)
async def clear_conversations(
    session: AsyncSession = Depends(get_session),
) -> ConversationClearOut:
    """一次清掉全部会话及其摘要，供开发期清理测试数据。

    长期记忆、每日回顾、关注事项和时间线是独立的用户数据，故意保留；否则一个
    看似清空对话的按钮会把已经沉淀的内容也一起抹掉。
    """
    conversation_ids = list(
        (await session.execute(select(Conversation.id))).scalars()
    )
    if not conversation_ids:
        return ConversationClearOut(
            deleted_conversations=0, deleted_messages=0, deleted_summaries=0
        )

    message_ids = list(
        (
            await session.execute(
                select(Message.id).where(Message.conversation_id.in_(conversation_ids))
            )
        ).scalars()
    )
    # 这些表没有都配置 ORM relationship，显式删掉能保证 SQLite 和 Postgres 的
    # 行为一致；附件正文是内容寻址共享文件，不能因为删行而误删磁盘文件。
    attachment_conditions = [Attachment.conversation_id.in_(conversation_ids)]
    if message_ids:
        attachment_conditions.append(Attachment.message_id.in_(message_ids))
    await session.execute(sa_delete(Attachment).where(or_(*attachment_conditions)))
    summary_result = await session.execute(
        sa_delete(ConversationSummary).where(
            ConversationSummary.conversation_id.in_(conversation_ids)
        )
    )
    message_result = await session.execute(
        sa_delete(Message).where(Message.conversation_id.in_(conversation_ids))
    )
    conversation_result = await session.execute(
        sa_delete(Conversation).where(Conversation.id.in_(conversation_ids))
    )
    return ConversationClearOut(
        deleted_conversations=conversation_result.rowcount or 0,
        deleted_messages=message_result.rowcount or 0,
        deleted_summaries=summary_result.rowcount or 0,
    )


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
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("model_profile_id") is not None:
        settings = await resolve_settings(session)
        try:
            await resolve_model_target(
                session, settings, profile_id=changes["model_profile_id"]
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    for field, value in changes.items():
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
    """撤下 id 大于 ``after`` 的所有消息，用于「重新生成」和「编辑重发」。

    ``after=0`` 清空整个会话。截断后可能留下没有配对结果的 tool_use，
    但 ``sanitize_history`` 在加载历史时会补齐，不用担心把会话截坏。

    **是软删除，不是 DELETE**。那一轮里模型可能已经写了长期记忆、提了会推送到
    手机的时间线事项 —— 这些都撤不回来，消息行再删掉就彻底断了溯源的线索。
    行留着，读历史的地方靠 ``live_message()`` 过滤。已经撤下的重复调用不再计数，
    所以 ``deleted`` 是「这次撤下了几条」。
    """
    await _require_conversation(session, conversation_id)
    result = await session.execute(
        sa_update(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.id > after,
            live_message(),
        )
        .values(deleted_at=dt.datetime.now(dt.UTC))
    )
    deleted = result.rowcount or 0
    if deleted:
        logger.info("✁ conv#%s 撤下 %d 条消息 (after=%s)", conversation_id, deleted, after)
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
    """按天汇总 token 用量。字段名各家不同，这里统一归一化。

    **一条查询取回整个窗口，再在 Python 里分桶。** 原先是每天一条 SELECT，
    days=90 就是 90 次往返，而 review 页正是拉长窗口的那个入口。

    分桶不下推到 SQL 是有意的：usage 的字段名各家不同（见 ``_pick`` 的回退链），
    用 SQL 表达那套逻辑既难读又会绑死 Postgres 的 JSONB，而测试跑在 SQLite 上。
    单人使用，窗口内是千级的行，取回来算很划算。
    """
    span = min(days, 90)
    if span <= 0:
        return []

    today = dt.date.today()
    start, _ = local_day_bounds(today - dt.timedelta(days=span - 1))
    _, end = local_day_bounds(today)

    # 先把每一天摆好，没有消息的日子也要出现在结果里（前端按天画图，缺天会错位）
    buckets = {
        (today - dt.timedelta(days=offset)).isoformat(): DailyUsageOut(
            day=(today - dt.timedelta(days=offset)).isoformat(),
            messages=0,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
        )
        for offset in range(span)
    }

    # 这里**故意不加** `live_message()`：被编辑撤下的那轮 token 是真花掉了的，
    # 从用量统计里抹掉只会让账单对不上。这是唯一一个该看全量消息的地方。
    stmt = select(Message.created_at, Message.usage).where(
        Message.usage.is_not(None),
        Message.created_at >= start,
        Message.created_at < end,
    )
    for created_at, usage in (await session.execute(stmt)).all():
        if not usage:
            continue
        bucket = buckets.get(_local_day(created_at).isoformat())
        if bucket is None:
            continue
        bucket.messages += 1
        bucket.input_tokens += _pick(usage, "input_tokens", "prompt_tokens")
        bucket.output_tokens += _pick(usage, "output_tokens", "completion_tokens")
        bucket.cached_tokens += _pick(
            usage, "cache_read_input_tokens", "prompt_cache_hit_tokens"
        )

    # 保持原来的顺序：今天在最前面
    return list(buckets.values())


def _local_day(moment: dt.datetime) -> dt.date:
    """时间戳落在本地的哪一天。

    时间戳按 UTC 存，但「今天用了多少」对用户是本地概念 —— 和 local_day_bounds
    同一个理由。SQLite 不保存时区，取回来是 naive 的；Postgres 是 aware。
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.UTC)
    return moment.astimezone().date()


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
        .where(Message.conversation_id == conversation_id, live_message())
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
        # no-transform 是拦住中间层压缩的标准信号，这里必须有：前端容器用 Next 的
        # /backend 代理转发，而 Next 在代理前无条件挂了 gzip 中间件，它只认
        # no-transform（X-Accel-Buffering 只有 nginx 认）。少了它，分片会攒到
        # gzip 的 1KB 阈值才发一次，前端看着就不是流式的。
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


# 按会话串行化。并发跑同一个会话会让历史错乱：两个请求各自读到同一份历史，
# 各自追加，结果变成 user说B → user说A → assistant B → assistant A，
# 两条回复都只看到半边上下文。双击发送按钮就能触发。
#
# **这是进程内的锁**，只在单 worker 下成立 —— 本项目就是单人单进程（compose 里
# uvicorn 不带 --workers），要上多进程的话这套得换成数据库层的咨询锁。
_locks: dict[int, asyncio.Lock] = {}


def _conversation_lock(conversation_id: int) -> asyncio.Lock:
    lock = _locks.get(conversation_id)
    if lock is None:
        lock = _locks[conversation_id] = asyncio.Lock()
    return lock


def _release_lock(conversation_id: int) -> None:
    """没人在用了就把锁丢掉，别让字典随会话数一直涨。

    只在没上锁时删：等锁的那一方**已经持有 _locks 里的这个对象**，此时换一把新的
    会让两个请求各拿各的锁，串行化就失效了 —— 正是这把锁要防的事。
    """
    lock = _locks.get(conversation_id)
    if lock is not None and not lock.locked():
        del _locks[conversation_id]


async def _stream(payload: ChatRequest) -> AsyncIterator[str]:
    """SSE 生成器自己持有数据库会话。

    不能用 Depends(get_session)：请求处理函数返回后依赖就会被清理，而生成器此时才刚开始跑。
    """
    conversation_id = payload.conversation_id
    if conversation_id is None:
        async with get_sessionmaker()() as session:
            conversation = Conversation()
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
            conversation_id = conversation.id
            yield _sse({"type": "conversation", "conversation": ConversationOut.model_validate(conversation, from_attributes=True).model_dump(mode="json")})

    lock = _conversation_lock(conversation_id)
    if lock.locked():
        # 直接拒绝而不排队：双击场景下第二条本来就是误触，
        # 让它排队等上一轮跑完再答一遍反而莫名其妙。
        yield _sse({"type": "error", "message": "该会话正在生成中，请等当前回答结束"})
        return

    try:
        async with lock, get_sessionmaker()() as session:
            try:
                conversation = await session.get(Conversation, conversation_id)
                if conversation is None:
                    yield _sse({"type": "error", "message": "会话不存在"})
                    return

                # 每轮重新解析：设置页改完不用重启就能生效。会话选择优先于全局默认。
                settings = await resolve_settings(session)
                if payload.web_search and not is_configured(settings.tavily_api_key):
                    yield _sse({"type": "error", "message": "联网搜索未配置，请在后端 .env 设置 TAVILY_API_KEY 后重启服务"})
                    return
                target = await resolve_model_target(
                    session,
                    settings,
                    profile_id=payload.model_profile_id or conversation.model_profile_id,
                )
                if not target.supports_tools:
                    yield _sse({"type": "error", "message": "当前模型不支持工具调用，无法运行记忆和时间线功能"})
                    return
                if target.profile_id is not None:
                    conversation.model_profile_id = target.profile_id

                # 看图的能力从 target 上问，不问厂商名 —— 换成 Claude 之后
                # vision_target 根本不会被用到，这条链路自动退场。
                vision_target = (
                    None
                    if target.supports_vision
                    else await resolve_vision_target(session, settings)
                )
                if (
                    payload.attachment_ids
                    and not target.supports_vision
                    and vision_target is None
                ):
                    # 明确报错而不是把图悄悄丢掉。静默丢弃的症状是模型答非所问，
                    # 而用户根本不会想到是图没发出去。
                    yield _sse({"type": "error", "message": "当前模型看不了图片，请在设置页配置一个视觉模型档案，或换用支持视觉的聊天模型"})
                    return

                # 工具、提示词、provider 的装配在 app/agent.py，和每日整理共用一份 ——
                # 各写一份迟早会长成「提示词讲了某个工具但没注册它」。
                context = await build_agent_context(
                    session,
                    settings=settings,
                    target=target,
                    purpose="chat",
                    conversation_id=conversation.id,
                    web_search=payload.web_search,
                )
                service = ChatService(
                    session=session,
                    provider=context.provider,
                    executor=context.executor,
                    settings=settings,
                    model_profile_id=target.profile_id,
                    # 配了 ZHIPU_API_KEY 才有；没配则为 None，标题退回聊天 provider
                    title_client=get_title_client(settings),
                    hydrator=AttachmentHydrator(
                        session,
                        settings,
                        target=target,
                        vision_target=vision_target,
                    ),
                )
                system = context.system

                # The browser gets the ID only as an SSE metadata event. It
                # never needs Phoenix's API or collector endpoint.
                trace_id = current_trace_id()
                if trace_id:
                    yield _sse({"type": "trace", "trace_id": trace_id})

                async for event in service.stream_reply(
                    conversation=conversation,
                    system=system,
                    user_text=payload.content,
                    attachment_ids=payload.attachment_ids,
                ):
                    yield _sse(_to_payload(event))
            except Exception as exc:
                logger.exception("对话流处理失败")
                await session.rollback()
                yield _sse({"type": "error", "message": f"服务端错误：{exc}"})
    finally:
        # 客户端断开也会走到这里（生成器被关闭）。不清理的话 _locks 会随会话数只增不减。
        _release_lock(conversation_id)


def _to_payload(event: Any) -> dict[str, Any]:
    if isinstance(event, tuple):
        name, data = event
        return {"type": name, **data}
    if is_dataclass(event) and not isinstance(event, type):
        return asdict(event)
    return {"type": "error", "message": f"未知事件 {event!r}"}


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
