"""DeepSeek（OpenAI 兼容接口）的 provider 实现。

内部消息格式统一用 Anthropic 的 content block 数组作为「标准格式」，
这里负责双向翻译。这样换 provider 不会让已有的对话历史失效。

和 Anthropic 版的差异：
- 没有原生记忆工具，用 memory/tool.py 里手写的 function schema
- 思考内容走 ``reasoning_content`` 字段，且**不能回传**（回传会 400），所以翻译时丢弃
- 上下文缓存是自动的，不需要也不支持 ``cache_control``
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from app import debug
from app.config import Settings, get_settings
from app.debug.log import log_request
from app.llm.events import (
    AgentEvent,
    AssistantTurn,
    Done,
    Error,
    TextDelta,
    ThinkingDelta,
    ToolResult,
    ToolResultTurn,
    ToolUse,
)
from app.llm.provider import ToolExecutor

logger = logging.getLogger(__name__)


@dataclass
class _Turn:
    """一轮响应里除了已经流出去的分片之外，还需要留到轮次结束才能用的东西。

    text / reasoning 同时也要留 —— 分片发给了前端，落库还得用完整的那份。
    """

    text: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, str]] = field(default_factory=list)
    finish: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class DeepSeekProvider:
    def __init__(
        self, settings: Settings | None = None, client: AsyncOpenAI | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or AsyncOpenAI(
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url,
        )

    async def run(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        executor: ToolExecutor | None = None,
        thinking: bool | None = None,
    ) -> AsyncIterator[AgentEvent]:
        working = list(messages)
        tools = executor.openai_definitions if executor is not None else []
        total_usage: dict[str, int] = {}
        want_thinking = (
            self.settings.deepseek_thinking if thinking is None else thinking
        )

        for iteration in range(self.settings.max_tool_iterations):
            request: dict[str, Any] = {
                "model": self.settings.deepseek_model,
                "max_tokens": self.settings.deepseek_max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    *to_openai_messages(working),
                ],
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if tools:
                request["tools"] = tools
            if not want_thinking:
                # DeepSeek 用和 Anthropic 一样的形状，但它不是 OpenAI 标准字段 ——
                # SDK 的 create() 会拒绝未知 kwarg，必须走 extra_body 透传。
                # 不传就是默认开着，所以只在要关的时候发。
                # （reasoning_effort 是标准字段但 DeepSeek 静默忽略，别用。）
                request["extra_body"] = {"thinking": {"type": "disabled"}}

            # 记录的就是下面那个 request 本身，不另拼一份
            snapshot = (
                debug.recorder.record(
                    provider="deepseek",
                    model=self.settings.deepseek_model,
                    payload=request,
                    iteration=iteration,
                )
                if self.settings.debug_prompts
                else None
            )
            if snapshot is not None:
                log_request(logger, snapshot)

            turn = _Turn()
            try:
                # 边收边吐 —— 攒完再发等于把上游的流式白白吃掉。
                async for delta in self._consume(request, turn):
                    yield delta
            except Exception as exc:
                logger.exception("DeepSeek 调用失败")
                if snapshot is not None:
                    snapshot.finish(error=str(exc))
                yield Error(message=f"模型调用失败：{exc}")
                return

            if snapshot is not None:
                snapshot.finish(usage=turn.usage, stop_reason=turn.finish)

            _accumulate(total_usage, turn.usage)
            assistant_content = to_content_blocks(
                "".join(turn.text), "".join(turn.reasoning), turn.tool_calls
            )
            working.append({"role": "assistant", "content": assistant_content})
            yield AssistantTurn(
                content=assistant_content, usage=turn.usage, stop_reason=turn.finish
            )

            tool_calls = turn.tool_calls
            if not tool_calls:
                yield Done(usage=total_usage)
                return

            if executor is None:
                yield Error(message="模型请求了工具，但当前会话未启用任何工具。")
                return

            results: list[dict[str, Any]] = []
            for call in tool_calls:
                tool_input, parse_error = _parse_arguments(call["arguments"])
                yield ToolUse(name=call["name"], input=tool_input)

                if parse_error:
                    text_out, is_error = parse_error, True
                else:
                    text_out, is_error = await executor.execute(
                        call["name"], tool_input
                    )

                yield ToolResult(
                    name=call["name"], ok=not is_error, summary=_clip(text_out, 200)
                )
                result: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": text_out,
                }
                if is_error:
                    result["is_error"] = True
                results.append(result)

            working.append({"role": "user", "content": results})
            yield ToolResultTurn(content=results)

        yield Error(
            message=f"超过单次请求的最大工具轮次（{self.settings.max_tool_iterations}）。"
        )

    async def _consume(
        self, request: dict[str, Any], out: _Turn
    ) -> AsyncIterator[AgentEvent]:
        """消费一次流式响应：文本 / 思考分片当场产出，其余拼装进 ``out``。

        工具调用不能这么发 —— arguments 是逐片下发的 JSON，凑不齐就没法解析，
        所以只有它必须攒到流结束。
        """
        # 工具调用的 arguments 是逐片流式下发的，按 index 累积。
        slots: dict[int, dict[str, str]] = {}

        stream = await self.client.chat.completions.create(**request)
        async for chunk in stream:
            if chunk.usage is not None:
                out.usage = chunk.usage.model_dump(exclude_none=True)
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            if choice.finish_reason:
                out.finish = choice.finish_reason

            delta = choice.delta
            if delta is None:
                continue
            if getattr(delta, "reasoning_content", None):
                out.reasoning.append(delta.reasoning_content)
                yield ThinkingDelta(text=delta.reasoning_content)
            if delta.content:
                out.text.append(delta.content)
                yield TextDelta(text=delta.content)

            for call in delta.tool_calls or []:
                slot = slots.setdefault(
                    call.index, {"id": "", "name": "", "arguments": ""}
                )
                if call.id:
                    slot["id"] = call.id
                if call.function and call.function.name:
                    slot["name"] = call.function.name
                if call.function and call.function.arguments:
                    slot["arguments"] += call.function.arguments

        out.tool_calls = [slots[i] for i in sorted(slots)]

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
        thinking: bool = True,
    ) -> str:
        request: dict[str, Any] = {
            "model": self.settings.deepseek_model,
            "max_tokens": max_tokens or self.settings.deepseek_max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if not thinking:
            # 和 run() 同一个形状：不是 OpenAI 标准字段，必须走 extra_body 透传，
            # 而且不传就是默认开着，所以只在要关的时候发。
            request["extra_body"] = {"thinking": {"type": "disabled"}}
        response = await self.client.chat.completions.create(**request)
        choice = response.choices[0]
        if choice.finish_reason == "length":
            logger.warning("补全被 max_tokens 截断，产出可能不完整")
        return (choice.message.content or "").strip()


# ---------- 标准格式（Anthropic content blocks）与 OpenAI 消息的互转 ----------


def to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """content block 数组 → OpenAI 消息数组。

    thinking 块会被丢弃：DeepSeek 不接受 reasoning_content 回传。
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        blocks = message["content"]
        if isinstance(blocks, str):
            out.append({"role": role, "content": blocks})
            continue

        tool_results = [b for b in blocks if b.get("type") == "tool_result"]
        if tool_results:
            # 每个 tool_result 都是独立的一条 tool 消息。
            out.extend(
                {
                    "role": "tool",
                    "tool_call_id": block["tool_use_id"],
                    "content": _as_text(block.get("content", "")),
                }
                for block in tool_results
            )
            continue

        text = "\n".join(
            b.get("text", "") for b in blocks if b.get("type") == "text"
        ).strip()

        if role == "assistant":
            tool_calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {
                        "name": b["name"],
                        "arguments": json.dumps(b.get("input", {}), ensure_ascii=False),
                    },
                }
                for b in blocks
                if b.get("type") == "tool_use"
            ]
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
        else:
            out.append({"role": role, "content": text})
    return out


def to_content_blocks(
    text: str, reasoning: str, tool_calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """OpenAI 响应 → content block 数组（落库用的标准格式）。"""
    blocks: list[dict[str, Any]] = []
    if reasoning:
        # 没有 signature —— 那是 Anthropic 特有的，这里也不需要回传。
        blocks.append({"type": "thinking", "thinking": reasoning})
    if text:
        blocks.append({"type": "text", "text": text})
    for call in tool_calls:
        tool_input, _ = _parse_arguments(call["arguments"])
        blocks.append(
            {
                "type": "tool_use",
                "id": call["id"],
                "name": call["name"],
                "input": tool_input,
            }
        )
    return blocks


def _parse_arguments(raw: str) -> tuple[dict[str, Any], str]:
    """解析工具参数。模型偶尔会吐出非法 JSON，这时把错误回给它自己纠正。"""
    if not raw:
        return {}, ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"工具参数不是合法 JSON：{exc}"
    if not isinstance(parsed, dict):
        return {}, f"工具参数必须是对象，收到 {type(parsed).__name__}"
    return parsed, ""


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        ).strip()
    return str(content)


def _accumulate(total: dict[str, int], usage: dict[str, Any]) -> None:
    for key, value in usage.items():
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"
