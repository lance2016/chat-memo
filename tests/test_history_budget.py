"""历史裁剪：长会话不能无限膨胀，裁完还必须是两家 API 都收的形状。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.service import ChatService, trim_history
from app.config import Settings
from app.db.models import Conversation, Message
from app.llm.anthropic_provider import AnthropicProvider
from tests.fakes import FakeAnthropic


def user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def assistant(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def tool_call(tool_id: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": tool_id, "name": "memory", "input": {}}
        ],
    }


def tool_result(tool_id: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}
        ],
    }


def test_within_budget_is_untouched() -> None:
    messages = [user("你好"), assistant("在的")]
    assert trim_history(messages, 100_000) == messages


def test_drops_oldest_rounds_when_over_budget() -> None:
    messages = [user(f"第{i}轮" + "填" * 400) for i in range(10)]
    for index in range(1, len(messages), 2):
        messages[index] = assistant("回应" + "填" * 400)

    trimmed = trim_history(messages, 3_000)

    assert len(trimmed) < len(messages)
    # 丢的是最老的：最后一条必须还在
    assert trimmed[-1] == messages[-1]
    assert trimmed == messages[len(messages) - len(trimmed) :]


def test_kept_window_starts_with_a_user_message() -> None:
    """Anthropic 要求 messages 以 user 开头，按体量截断很容易正好切在 assistant 上。"""
    messages = [user("很老的问题" + "填" * 900), assistant("很老的回答" + "填" * 900)]
    messages += [user("新问题"), assistant("新回答")]

    trimmed = trim_history(messages, 1_000)

    assert trimmed[0]["role"] == "user"


def test_never_starts_with_an_orphan_tool_result() -> None:
    """配对的 tool_use 被丢掉后，孤立的 tool_result 会让整个请求 400。"""
    messages = [
        user("旧问题" + "填" * 2_000),
        tool_call("toolu_1"),
        tool_result("toolu_1"),
        assistant("旧回答"),
        user("新问题"),
    ]

    trimmed = trim_history(messages, 200)

    assert trimmed[0]["role"] == "user"
    first_types = {block["type"] for block in trimmed[0]["content"]}
    assert "tool_result" not in first_types


def test_last_message_survives_even_if_it_alone_exceeds_budget() -> None:
    """预算再紧也得留下最后一条，否则这轮没有输入可发。"""
    messages = [user("旧"), assistant("巨长的回答" + "填" * 5_000)]

    trimmed = trim_history(messages, 10)

    assert trimmed == [messages[-1]] or trimmed[-1] == messages[-1]
    assert trimmed


def test_disabled_when_budget_is_zero() -> None:
    messages = [user("一"), assistant("二"), user("三")]
    assert trim_history(messages, 0) == messages


async def test_load_history_applies_the_budget(session: AsyncSession) -> None:
    """端到端：service 真的把预算用上了，而不只是有个能跑的纯函数。"""
    conversation = Conversation()
    session.add(conversation)
    await session.flush()

    for index in range(8):
        session.add(
            Message(
                conversation_id=conversation.id,
                role="user" if index % 2 == 0 else "assistant",
                content=[{"type": "text", "text": "填" * 500}],
                search_text="填",
            )
        )
    await session.flush()

    service = ChatService(
        session=session,
        provider=AnthropicProvider(
            settings=Settings(anthropic_api_key="test"), client=FakeAnthropic([])
        ),
        settings=Settings(history_max_chars=2_000),
    )
    history = await service.load_history(conversation.id)

    assert 0 < len(history) < 8
    assert history[0]["role"] == "user"
