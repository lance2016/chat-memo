"""针对复查中发现的具体缺陷的回归测试。"""

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def messages_of(session: AsyncSession, conversation_id: int) -> list[Message]:
    stmt = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
    return list((await session.execute(stmt)).scalars())
