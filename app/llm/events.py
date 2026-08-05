"""Agent loop 向上层吐出的事件。

chat 层把它们转成 SSE 推给前端，同时用带 ``content`` 的那几个事件做落库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ThinkingDelta:
    text: str
    type: str = "thinking_delta"


@dataclass(frozen=True)
class TextDelta:
    text: str
    type: str = "text_delta"


@dataclass(frozen=True)
class ToolUse:
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass(frozen=True)
class ToolResult:
    name: str
    ok: bool
    summary: str
    type: str = "tool_result"


@dataclass(frozen=True)
class AssistantTurn:
    """模型一轮完整输出。``content`` 必须原样落库并在后续轮次回传。"""

    content: list[dict[str, Any]]
    usage: dict[str, Any]
    stop_reason: str | None
    type: str = "assistant_turn"


@dataclass(frozen=True)
class ToolResultTurn:
    """回给模型的 tool_result 消息，同样需要落库。"""

    content: list[dict[str, Any]]
    type: str = "tool_result_turn"


@dataclass(frozen=True)
class Done:
    usage: dict[str, Any] = field(default_factory=dict)
    type: str = "done"


@dataclass(frozen=True)
class Error:
    message: str
    type: str = "error"


AgentEvent = (
    ThinkingDelta
    | TextDelta
    | ToolUse
    | ToolResult
    | AssistantTurn
    | ToolResultTurn
    | Done
    | Error
)
