"""针对复查中发现的具体缺陷的回归测试。"""

import asyncio
import datetime as dt
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.router import (
    ChatRequest,
    _conversation_lock,
    _locks,
    _release_lock,
    chat,
)
from app.chat.service import DEFAULT_TITLE, ChatService
from app.config import Settings
from app.db.models import Conversation, Message
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.events import Done, TextDelta, ThinkingDelta
from tests.fakes import FakeAnthropic, text_turn, thinking_then_text


def provider_with(turns: list) -> AnthropicProvider:
    return AnthropicProvider(
        settings=Settings(anthropic_api_key="test"), client=FakeAnthropic(turns)
    )


async def make_conversation(session: AsyncSession) -> Conversation:
    conversation = Conversation()
    session.add(conversation)
    await session.flush()
    return conversation


async def drain(service: ChatService, conversation: Conversation, text: str) -> list[Any]:
    return [
        event
        async for event in service.stream_reply(
            conversation=conversation, system="sys", user_text=text
        )
    ]


async def test_degenerate_title_does_not_crash(session: AsyncSession) -> None:
    """模型只回了一对引号时，标题解析不能抛 IndexError。"""
    conversation = await make_conversation(session)
    service = ChatService(session, provider_with([text_turn("答"), text_turn('""')]))

    events = await drain(service, conversation, "问")

    assert not any(isinstance(e, tuple) and e[0] == "title" for e in events)
    assert conversation.title == DEFAULT_TITLE


async def test_title_strips_xml_leakage(session: AsyncSession) -> None:
    """thinking 标签泄漏进标题时要清掉，不能直接当标题用。"""
    conversation = await make_conversation(session)
    service = ChatService(
        session,
        provider_with([text_turn("答"), text_turn("<thinking>想想</thinking>\n聊技术选型")]),
    )

    await drain(service, conversation, "问")
    assert conversation.title == "聊技术选型"


async def test_new_message_bumps_conversation_updated_at(
    session: AsyncSession,
) -> None:
    """侧边栏按 updated_at 倒序，聊天必须让会话冒到最上面。"""
    conversation = await make_conversation(session)
    # 锚一个明确的旧时间，避免依赖 SQLite 只到秒的时间精度。
    before = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    conversation.updated_at = before
    await session.commit()

    service = ChatService(session, provider_with([text_turn("答"), text_turn("标题")]))
    await drain(service, conversation, "问")

    await session.refresh(conversation)
    assert conversation.updated_at.replace(tzinfo=dt.UTC) > before


async def test_title_arrives_before_done(session: AsyncSession) -> None:
    """done 是终止事件，前端收到它就会停止读流 —— title 必须排在它前面。"""
    conversation = await make_conversation(session)
    service = ChatService(session, provider_with([text_turn("答"), text_turn("标题")]))

    events = await drain(service, conversation, "问")
    kinds = [e[0] if isinstance(e, tuple) else type(e).__name__ for e in events]

    assert "title" in kinds
    assert kinds.index("title") < kinds.index("Done")
    assert isinstance(events[-2], Done)
    assert events[-1][0] == "message_id"


async def test_slow_title_does_not_hold_the_stream_open(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """标题拖太久就放手：它不该延长用户的等待。

    正文说完后流还开着，会话锁就还占着、前端的输入框也还是禁用的（要等流关闭）。
    裸 await 会把标题的尾延迟原样转嫁给用户，实测见过多等 7.7s。
    超时后标题保持 DEFAULT_TITLE，下一轮会自动再试。
    """
    monkeypatch.setattr("app.chat.service.TITLE_TIMEOUT", 0.05)
    conversation = await make_conversation(session)
    service = ChatService(session, provider_with([text_turn("答")]))

    async def never_returns(_first_text: str) -> str:
        await asyncio.sleep(30)
        return "来不及的标题"

    monkeypatch.setattr(service, "_complete_title", never_returns)

    events = await drain(service, conversation, "问")
    kinds = [e[0] if isinstance(e, tuple) else type(e).__name__ for e in events]

    assert "title" not in kinds  # 没等到，就不推给前端
    assert isinstance(events[-2], Done)  # 但这一轮照常收尾
    assert events[-1][0] == "message_id"
    assert conversation.title == DEFAULT_TITLE  # 留给下一轮重试


async def test_interrupted_turn_keeps_streamed_text(session: AsyncSession) -> None:
    """中断时用户已经看到的正文必须留下，否则刷新页面就凭空消失。"""
    conversation = await make_conversation(session)
    service = ChatService(session, provider_with([text_turn("这是已经流出去的正文")]))

    stream = service.stream_reply(
        conversation=conversation, system="sys", user_text="问"
    )
    # 消费到第一个 text_delta 就模拟客户端断开
    async for event in stream:
        if isinstance(event, TextDelta):
            break
    await stream.aclose()

    rows = await messages_of(session, conversation.id)
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[1].content[0]["text"] == "这是已经流出去的正文"
    assert rows[1].usage == {"interrupted": True}


async def test_completed_turn_not_double_saved(session: AsyncSession) -> None:
    """正常跑完的一轮不能再被中断兜底逻辑重复存一次。"""
    conversation = await make_conversation(session)
    service = ChatService(session, provider_with([text_turn("答"), text_turn("标题")]))
    await drain(service, conversation, "问")

    rows = await messages_of(session, conversation.id)
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[1].usage != {"interrupted": True}


async def test_interrupted_before_any_text_saves_no_empty_reply(
    session: AsyncSession,
) -> None:
    """还在思考阶段就被掐断，不该留下一条空的助手消息。"""
    conversation = await make_conversation(session)
    service = ChatService(session, provider_with([thinking_then_text("想", "答")]))

    stream = service.stream_reply(
        conversation=conversation, system="sys", user_text="问"
    )
    async for event in stream:
        if isinstance(event, ThinkingDelta):
            break  # 正文还没开始
    await stream.aclose()

    rows = await messages_of(session, conversation.id)
    assert [r.role for r in rows] == ["user"]


async def test_sse_headers_opt_out_of_proxy_compression() -> None:
    """SSE 响应必须带 ``no-transform``，否则中间层的 gzip 会把流式压没了。

    容器里浏览器走 Next 的 ``/backend`` 代理，而 Next 在转发前无条件挂了 gzip
    中间件：``text/event-stream`` 会被判定为可压缩，分片攒到 1KB 阈值才发一次，
    且它从不主动 flush —— 前端看到的就是「转圈很久，然后整段蹦出来」。
    这个中间件只认 ``no-transform``（``X-Accel-Buffering`` 只有 nginx 认），
    所以少了这个 token，真流式的后端在浏览器里依然表现成伪流式。
    """
    response = await chat(ChatRequest(conversation_id=1, content="问"))

    assert response.media_type == "text/event-stream"
    assert "no-transform" in response.headers["cache-control"]


async def test_conversation_lock_is_dropped_once_nobody_holds_it() -> None:
    """``_locks`` 不能随会话数只增不减 —— 每个会话留一把锁，永远不回收。"""
    _locks.clear()
    lock = _conversation_lock(4242)

    async with lock:
        # 持有期间不能回收，否则等锁的人会拿到另一把锁
        _release_lock(4242)
        assert _locks.get(4242) is lock

    _release_lock(4242)
    assert 4242 not in _locks


async def test_releasing_a_held_lock_keeps_serialization_intact() -> None:
    """回收若不看「是否仍被持有」，双击发送就会各拿各的锁，串行化直接失效。

    这是这把锁存在的唯一理由：两个请求各读到同一份历史、各自追加，
    结果是 user说B → user说A → assistant B → assistant A，两条回复都只看到半边上下文。
    """
    _locks.clear()
    first = _conversation_lock(99)
    await first.acquire()
    try:
        _release_lock(99)
        # 第二个请求必须看到同一个对象，才会被 lock.locked() 挡下来
        assert _conversation_lock(99) is first
        assert _conversation_lock(99).locked()
    finally:
        first.release()
        _release_lock(99)


async def messages_of(session: AsyncSession, conversation_id: int) -> list[Message]:
    stmt = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
    return list((await session.execute(stmt)).scalars())
