"""Anthropic 流式接口的替身，用来在没有 API key 的情况下验证 agent loop。

形状对齐真实 SDK：``messages.stream(...)`` 同步返回一个异步上下文管理器，
迭代它得到流事件，``get_final_message()`` 给出组装好的消息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeDelta:
    type: str
    text: str = ""
    thinking: str = ""


@dataclass
class FakeStreamEvent:
    type: str
    delta: FakeDelta | None = None


@dataclass
class FakeBlock:
    type: str
    text: str | None = None
    thinking: str | None = None
    signature: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None

    def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
        data = {
            "type": self.type,
            "text": self.text,
            "thinking": self.thinking,
            "signature": self.signature,
            "id": self.id,
            "name": self.name,
            "input": self.input,
        }
        return {k: v for k, v in data.items() if not exclude_none or v is not None}


@dataclass
class FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 5
    cache_read_input_tokens: int = 0

    def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }


@dataclass
class FakeMessage:
    content: list[FakeBlock]
    stop_reason: str = "end_turn"
    usage: FakeUsage = field(default_factory=FakeUsage)


class FakeStreamManager:
    def __init__(self, events: list[FakeStreamEvent], final: FakeMessage) -> None:
        self._events = events
        self._final = final

    async def __aenter__(self) -> FakeStreamManager:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def __aiter__(self):
        for event in self._events:
            yield event

    async def get_final_message(self) -> FakeMessage:
        return self._final


class FakeMessages:
    def __init__(self, turns: list[tuple[list[FakeStreamEvent], FakeMessage]]) -> None:
        self._turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> FakeStreamManager:
        self.calls.append(kwargs)
        if not self._turns:
            raise AssertionError("agent loop 请求的轮次超出了预设")
        events, final = self._turns.pop(0)
        return FakeStreamManager(events, final)


class FakeAnthropic:
    def __init__(self, turns: list[tuple[list[FakeStreamEvent], FakeMessage]]) -> None:
        self.messages = FakeMessages(turns)


def text_turn(text: str, stop_reason: str = "end_turn") -> tuple[list, FakeMessage]:
    events = [
        FakeStreamEvent("content_block_delta", FakeDelta("text_delta", text=text))
    ]
    return events, FakeMessage([FakeBlock("text", text=text)], stop_reason=stop_reason)


def thinking_then_text(thought: str, text: str) -> tuple[list, FakeMessage]:
    events = [
        FakeStreamEvent(
            "content_block_delta", FakeDelta("thinking_delta", thinking=thought)
        ),
        FakeStreamEvent("content_block_delta", FakeDelta("text_delta", text=text)),
    ]
    final = FakeMessage(
        [
            FakeBlock("thinking", thinking=thought, signature="sig-abc"),
            FakeBlock("text", text=text),
        ]
    )
    return events, final


def tool_turn(
    name: str, tool_input: dict[str, Any], tool_id: str = "toolu_1"
) -> tuple[list, FakeMessage]:
    final = FakeMessage(
        [FakeBlock("tool_use", id=tool_id, name=name, input=tool_input)],
        stop_reason="tool_use",
    )
    return [], final
