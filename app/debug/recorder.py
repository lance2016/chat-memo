"""记录**真正发给模型的那个请求体**，供排查用。

动机：system prompt 是拼出来的，历史是规整过的，运行时上下文是注进去的，
无签名 thinking 是被滤掉的 —— 中间经手的地方太多，光看数据库和代码猜不出
最终 payload 长什么样。这里在发请求的那一刻把原物留一份。

**存在进程内存里，不落库**。调试数据的价值只有几分钟，落库要建表、要清理、
要考虑里面有对话原文的隐私，代价远大于收益。重启即清空是特性不是缺陷。

默认关闭（``debug_prompts``）。开着会把完整对话历史留在内存里，
排查完记得关掉。
"""

from __future__ import annotations

import datetime as dt
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from itertools import count
from typing import Any

# 只留最近这么多次。一次回答带工具调用可能产生好几条，20 大约是三四轮对话。
CAPACITY = 20

# 由 ChatService 设置。provider 不知道会话 ID，又不该为了调试改它的签名。
current_conversation: ContextVar[int | None] = ContextVar(
    "current_conversation", default=None
)

_ids = count(1)


@dataclass
class RequestSnapshot:
    id: int
    at: dt.datetime
    provider: str
    model: str
    conversation_id: int | None
    # agent loop 里的第几次请求（0 = 用户这轮的第一次，之后每轮工具调用 +1）
    iteration: int
    payload: dict[str, Any]
    usage: dict[str, Any] = field(default_factory=dict)
    stop_reason: str = ""
    error: str = ""
    seconds: float = 0.0
    _started: float = field(default=0.0, repr=False)

    def finish(
        self,
        *,
        usage: dict[str, Any] | None = None,
        stop_reason: str | None = None,
        error: str = "",
    ) -> None:
        self.seconds = time.monotonic() - self._started
        self.usage = usage or {}
        self.stop_reason = stop_reason or ""
        self.error = error

    def summary(self) -> dict[str, Any]:
        """列表用的轻量版，不带 payload —— 完整历史动辄几十 KB。"""
        messages = self.payload.get("messages", self.payload.get("input", []))
        return {
            "id": self.id,
            "at": self.at.isoformat(),
            "provider": self.provider,
            "model": self.model,
            "conversation_id": self.conversation_id,
            "iteration": self.iteration,
            "messages": len(messages),
            "system_chars": len(_system_text(self.payload)),
            "tools": len(self.payload.get("tools", [])),
            "usage": self.usage,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "seconds": round(self.seconds, 2),
        }

    def detail(self) -> dict[str, Any]:
        return {**self.summary(), "payload": self.payload, "outline": outline(self.payload)}


class RequestRecorder:
    def __init__(self, capacity: int = CAPACITY) -> None:
        self._items: deque[RequestSnapshot] = deque(maxlen=capacity)

    @property
    def capacity(self) -> int:
        return self._items.maxlen or 0

    def record(
        self, *, provider: str, model: str, payload: dict[str, Any], iteration: int
    ) -> RequestSnapshot:
        snapshot = RequestSnapshot(
            id=next(_ids),
            at=dt.datetime.now(),
            provider=provider,
            model=model,
            conversation_id=current_conversation.get(),
            iteration=iteration,
            # payload 里的 messages 已经是新列表（strip_unsigned_thinking /
            # to_openai_messages 都返回新的），provider 之后只往 working 追加，
            # 不会就地改这里的内容，所以不用深拷贝。
            payload=payload,
            _started=time.monotonic(),
        )
        self._items.append(snapshot)
        return snapshot

    def list(
        self, *, conversation_id: int | None = None, limit: int = CAPACITY
    ) -> list[RequestSnapshot]:
        """最近的在前。"""
        items = [
            s
            for s in reversed(self._items)
            if conversation_id is None or s.conversation_id == conversation_id
        ]
        return items[:limit]

    def get(self, snapshot_id: int) -> RequestSnapshot | None:
        return next((s for s in self._items if s.id == snapshot_id), None)

    def clear(self) -> None:
        self._items.clear()


recorder = RequestRecorder()


# ---------- 可读轮廓 ----------


def _system_text(payload: dict[str, Any]) -> str:
    """两家的 system 位置不同：Anthropic 是顶层 list，OpenAI 是 messages[0]。"""
    system = payload.get("system")
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n".join(b.get("text", "") for b in system if isinstance(b, dict))
    if isinstance(payload.get("instructions"), str):
        return payload["instructions"]
    first = (payload.get("messages") or [{}])[0]
    if first.get("role") == "system":
        return _flatten(first.get("content"))
    return ""


def _flatten(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    return ""


def _preview(text: str, limit: int = 70) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _describe_block(block: Any) -> str:
    """一个 content block 压成一句话。"""
    if not isinstance(block, dict):
        return _preview(block)

    kind = block.get("type", "?")
    if kind == "text":
        return f'text({len(block.get("text", ""))}) {_preview(block.get("text", ""))}'
    if kind == "thinking":
        signed = "有签名" if block.get("signature") else "无签名"
        return f'thinking({len(block.get("thinking", ""))}, {signed})'
    if kind == "tool_use":
        args = block.get("input", {})
        hint = args.get("command", "") or block.get("name", "")
        return f'tool_use {hint} {_preview(args.get("path", ""), 40)}'.rstrip()
    if kind == "tool_result":
        flag = "✗" if block.get("is_error") else "✓"
        return f'tool_result {flag} {_preview(_flatten(block.get("content")) or block.get("content", ""))}'
    return f"{kind} {_preview(block)}"


def outline(payload: dict[str, Any]) -> list[str]:
    """把请求体渲染成每条消息一行，人眼能扫的轮廓。

    完整 payload 在 ``detail()`` 里；这个是给日志和调试面板列表用的。
    """
    lines: list[str] = []
    system = _system_text(payload)
    if system:
        lines.append(f"system({len(system)}) {_preview(system)}")

    messages = payload.get("messages", payload.get("input", []))
    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            lines.append(f"[{i}] input     {_preview(message)}")
            continue
        role = message.get("role", "?")
        if role == "system":
            continue  # 上面已经单独列过

        content = message.get("content")
        if isinstance(content, list):
            parts = [_describe_block(b) for b in content]
        elif content:
            parts = [_preview(content)]
        else:
            parts = []

        # OpenAI 形状：工具调用不在 content 里，在同级的 tool_calls 上
        for call in message.get("tool_calls", []) or []:
            fn = call.get("function", {})
            parts.append(f'tool_call {fn.get("name", "?")} {_preview(fn.get("arguments", ""), 40)}')

        if not parts:
            parts = ["(空)"]
        lines.append(f"[{i}] {role:<9} {parts[0]}")
        lines.extend(f"{'':<4} {'':<9} {p}" for p in parts[1:])

    for i, item in enumerate(messages):
        if not isinstance(item, dict) or item.get("type") not in {
            "function_call",
            "function_call_output",
        }:
            continue
        if item["type"] == "function_call":
            lines.append(
                f"[{i}] function  {item.get('name', '?')} "
                f"{_preview(item.get('arguments', ''), 40)}"
            )
        else:
            lines.append(f"[{i}] tool      {_preview(item.get('output', ''), 70)}")

    return lines
