from typing import Any

import pytest

from app.config import Settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.events import (
    AssistantTurn,
    Done,
    Error,
    TextDelta,
    ThinkingDelta,
    ToolResult,
    ToolResultTurn,
    ToolUse,
)
from app.memory.store import MemoryStore
from app.memory.tool import MemoryToolExecutor
from tests.fakes import FakeAnthropic, text_turn, thinking_then_text, tool_turn


def make_provider(turns: list, **overrides: Any) -> AnthropicProvider:
    settings = Settings(anthropic_api_key="test", **overrides)
    return AnthropicProvider(settings=settings, client=FakeAnthropic(turns))


async def collect(provider: AnthropicProvider, **kwargs: Any) -> list:
    return [
        event
        async for event in provider.run(
            system="you are a helper",
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            **kwargs,
        )
    ]


async def test_streams_text_and_finishes() -> None:
    provider = make_provider([text_turn("你好")])
    events = await collect(provider)

    assert isinstance(events[0], TextDelta) and events[0].text == "你好"
    assert isinstance(events[1], AssistantTurn)
    assert isinstance(events[-1], Done)


async def test_streams_thinking_before_text() -> None:
    provider = make_provider([thinking_then_text("先想想", "答案")])
    events = await collect(provider)

    assert isinstance(events[0], ThinkingDelta)
    assert isinstance(events[1], TextDelta)


async def test_assistant_turn_preserves_thinking_signature() -> None:
    """thinking 块及其签名必须原样保留，否则下一轮回传会 400。"""
    provider = make_provider([thinking_then_text("推理内容", "答案")])
    events = await collect(provider)

    turn = next(e for e in events if isinstance(e, AssistantTurn))
    thinking = next(b for b in turn.content if b["type"] == "thinking")
    assert thinking["thinking"] == "推理内容"
    assert thinking["signature"] == "sig-abc"


async def test_request_sets_cache_breakpoint_and_adaptive_thinking() -> None:
    provider = make_provider([text_turn("ok")])
    await collect(provider)

    call = provider.client.messages.calls[0]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["thinking"]["type"] == "adaptive"
    assert call["thinking"]["display"] == "summarized"


async def test_tool_loop_executes_and_feeds_result_back(session) -> None:
    store = MemoryStore(session, actor="test")
    provider = make_provider(
        [
            tool_turn(
                "memory",
                {
                    "command": "create",
                    "path": "/memories/profile/preferences.md",
                    "file_text": "用 uv，不用 pip",
                },
            ),
            text_turn("记下了"),
        ]
    )

    events = await collect(provider, executor=MemoryToolExecutor(store))
    kinds = [type(e).__name__ for e in events]
    assert kinds == [
        "AssistantTurn",
        "ToolUse",
        "ToolResult",
        "ToolResultTurn",
        "TextDelta",
        "AssistantTurn",
        "Done",
    ]

    tool_result = next(e for e in events if isinstance(e, ToolResult))
    assert tool_result.ok

    # 记忆真的写进去了
    assert "用 uv" in await store.view("/memories/profile/preferences.md")

    # 第二轮请求带上了完整历史：user、assistant(tool_use)、user(tool_result)
    second_call = provider.client.messages.calls[1]
    roles = [m["role"] for m in second_call["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert second_call["messages"][-1]["content"][0]["type"] == "tool_result"


async def test_tool_error_returns_is_error_not_exception(session) -> None:
    """路径逃逸这类错误要作为 is_error 回给模型，让它自己纠正，而不是炸掉整轮。"""
    store = MemoryStore(session, actor="test")
    provider = make_provider(
        [
            tool_turn("memory", {"command": "create", "path": "/etc/passwd", "file_text": "x"}),
            text_turn("换个路径再试"),
        ]
    )

    events = await collect(provider, executor=MemoryToolExecutor(store))

    tool_result = next(e for e in events if isinstance(e, ToolResult))
    assert not tool_result.ok

    turn = next(e for e in events if isinstance(e, ToolResultTurn))
    assert turn.content[0]["is_error"] is True
    assert isinstance(events[-1], Done)  # 对话继续，没有中断


async def test_tool_use_without_executor_errors() -> None:
    provider = make_provider([tool_turn("memory", {"command": "view", "path": "/memories"})])
    events = await collect(provider)
    assert isinstance(events[-1], Error)


async def test_iteration_cap_stops_runaway_loop(session) -> None:
    store = MemoryStore(session, actor="test")
    turns = [
        tool_turn("memory", {"command": "view", "path": "/memories"}) for _ in range(5)
    ]
    provider = make_provider(turns, max_tool_iterations=3)

    events = await collect(provider, executor=MemoryToolExecutor(store))
    assert isinstance(events[-1], Error)
    assert "最大工具轮次" in events[-1].message
    assert len(provider.client.messages.calls) == 3


async def test_api_failure_becomes_error_event() -> None:
    provider = make_provider([])  # 没有预设轮次 → stream() 抛异常
    events = await collect(provider)
    assert isinstance(events[-1], Error)


async def test_refusal_stops_with_error() -> None:
    provider = make_provider([text_turn("", stop_reason="refusal")])
    events = await collect(provider)
    assert isinstance(events[-1], Error)
    assert "拒绝" in events[-1].message


async def test_unknown_tool_name_is_error(session) -> None:
    store = MemoryStore(session, actor="test")
    provider = make_provider([tool_turn("bogus", {}), text_turn("ok")])
    events = await collect(provider, executor=MemoryToolExecutor(store))

    tool_result = next(e for e in events if isinstance(e, ToolResult))
    assert not tool_result.ok


@pytest.mark.parametrize(
    "payload",
    [
        {"command": "nope", "path": "/memories/a.md"},
        {"command": "create", "path": "/memories/a.md"},  # 缺 file_text
        {"command": "insert", "path": "/memories/a.md", "insert_line": "x", "insert_text": "y"},
    ],
)
async def test_malformed_tool_input_is_error(session, payload: dict) -> None:
    store = MemoryStore(session, actor="test")
    executor = MemoryToolExecutor(store)
    text, is_error = await executor.execute("memory", payload)
    assert is_error, text


async def test_tool_use_event_carries_input(session) -> None:
    store = MemoryStore(session, actor="test")
    provider = make_provider(
        [
            tool_turn("memory", {"command": "view", "path": "/memories"}),
            text_turn("空的"),
        ]
    )
    events = await collect(provider, executor=MemoryToolExecutor(store))

    tool_use = next(e for e in events if isinstance(e, ToolUse))
    assert tool_use.name == "memory"
    assert tool_use.input["command"] == "view"
