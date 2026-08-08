from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.service import DEFAULT_TITLE, ChatService
from app.config import Settings
from app.db.models import Conversation, Message
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.events import Done, TextDelta
from app.memory.store import MemoryStore
from app.memory.tool import MemoryToolExecutor
from tests.fakes import FakeAnthropic, text_turn, thinking_then_text, tool_turn


async def make_conversation(session: AsyncSession) -> Conversation:
    conversation = Conversation()
    session.add(conversation)
    await session.flush()
    return conversation


def provider_with(turns: list) -> AnthropicProvider:
    return AnthropicProvider(
        settings=Settings(anthropic_api_key="test"), client=FakeAnthropic(turns)
    )


async def messages_of(session: AsyncSession, conversation_id: int) -> list[Message]:
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    )
    return list((await session.execute(stmt)).scalars())


async def drain(service: ChatService, conversation: Conversation, text: str) -> list[Any]:
    return [
        event
        async for event in service.stream_reply(
            conversation=conversation, system="sys", user_text=text
        )
    ]


async def test_persists_user_and_assistant_turns(session: AsyncSession) -> None:
    conversation = await make_conversation(session)
    service = ChatService(
        session, provider_with([text_turn("你好"), text_turn("打个招呼")])
    )

    events = await drain(service, conversation, "hi")

    rows = await messages_of(session, conversation.id)
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[0].content == [{"type": "text", "text": "hi"}]
    assert rows[1].content[0]["text"] == "你好"
    assert rows[1].usage["output_tokens"] == 5

    assert any(isinstance(e, TextDelta) for e in events)
    assert any(isinstance(e, Done) for e in events)


async def test_persists_selected_model_profile(session: AsyncSession) -> None:
    conversation = await make_conversation(session)
    service = ChatService(
        session,
        provider_with([text_turn("答")]),
        model_profile_id=17,
    )

    await drain(service, conversation, "hi")

    rows = await messages_of(session, conversation.id)
    assert rows
    assert all(row.model_profile_id == 17 for row in rows)


async def test_internal_turn_events_are_not_streamed(session: AsyncSession) -> None:
    """AssistantTurn / ToolResultTurn 只用于落库，不该出现在 SSE 里。"""
    conversation = await make_conversation(session)
    service = ChatService(session, provider_with([text_turn("答"), text_turn("标题")]))

    events = await drain(service, conversation, "问")
    kinds = {type(e).__name__ for e in events if not isinstance(e, tuple)}
    assert "AssistantTurn" not in kinds
    assert "ToolResultTurn" not in kinds


async def test_emits_message_id_and_title(session: AsyncSession) -> None:
    conversation = await make_conversation(session)
    service = ChatService(session, provider_with([text_turn("答"), text_turn("聊聊天")]))

    events = await drain(service, conversation, "问")
    tuples = dict(e for e in events if isinstance(e, tuple))

    assert "message_id" in tuples
    assert tuples["title"]["title"] == "聊聊天"
    assert conversation.title == "聊聊天"


async def test_title_generated_only_once(session: AsyncSession) -> None:
    conversation = await make_conversation(session)
    conversation.title = "已有标题"

    service = ChatService(session, provider_with([text_turn("答")]))
    events = await drain(service, conversation, "问")

    assert not any(isinstance(e, tuple) and e[0] == "title" for e in events)
    assert conversation.title == "已有标题"


async def test_history_round_trips_thinking_blocks(session: AsyncSession) -> None:
    """第二轮必须把上一轮的 thinking 块（含签名）原样带回去。"""
    conversation = await make_conversation(session)

    first = ChatService(
        session, provider_with([thinking_then_text("想", "答一"), text_turn("标题")])
    )
    await drain(first, conversation, "问一")

    second_provider = provider_with([text_turn("答二")])
    second = ChatService(session, second_provider)
    await drain(second, conversation, "问二")

    sent = second_provider.client.messages.calls[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    thinking = next(b for b in sent[1]["content"] if b["type"] == "thinking")
    assert thinking["signature"] == "sig-abc"


async def test_tool_result_turn_is_persisted(session: AsyncSession) -> None:
    conversation = await make_conversation(session)
    store = MemoryStore(session, actor="chat")
    service = ChatService(
        session,
        provider_with(
            [
                tool_turn(
                    "memory",
                    {"command": "create", "path": "/memories/a.md", "file_text": "x"},
                ),
                text_turn("记好了"),
                text_turn("标题"),
            ]
        ),
        executor=MemoryToolExecutor(store),
    )

    await drain(service, conversation, "记一下")

    rows = await messages_of(session, conversation.id)
    assert [r.role for r in rows] == ["user", "assistant", "user", "assistant"]
    assert rows[2].content[0]["type"] == "tool_result"
    assert "x" in await store.view("/memories/a.md")


async def test_error_skips_title_generation(session: AsyncSession) -> None:
    conversation = await make_conversation(session)
    service = ChatService(session, provider_with([]))  # 立即失败

    events = await drain(service, conversation, "问")

    assert not any(isinstance(e, tuple) and e[0] == "title" for e in events)
    assert conversation.title == DEFAULT_TITLE
