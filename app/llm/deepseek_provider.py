"""DeepSeek（OpenAI 兼容接口）的 provider 实现。

内部消息格式统一用 Anthropic 的 content block 数组作为「标准格式」，
这里负责双向翻译。这样换 provider 不会让已有的对话历史失效。

和 Anthropic 版的差异：
- 没有原生记忆工具，用 memory/tool.py 里手写的 function schema
- 思考内容走 ``reasoning_content`` 字段；DeepSeek 的工具子轮次必须原样回传
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
from app.llm.target import ModelTarget
from app.obs import record_llm_input, record_llm_output, trace

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
        self,
        settings: Settings | None = None,
        client: AsyncOpenAI | None = None,
        target: ModelTarget | None = None,
    ) -> None:
        """`target` 说「调哪个模型」，`settings` 只剩「怎么调」。

        这个实现承载所有 OpenAI 兼容协议的服务（DeepSeek、硅基流动、OpenRouter、
        本地推理），差异全部由 target 的 base_url / api_key / model_id 表达。
        """
        self.settings = settings or get_settings()
        self.target = target or ModelTarget.from_settings(self.settings)
        self.client = client or AsyncOpenAI(
            api_key=self.target.api_key or "not-needed",
            base_url=self.target.base_url or None,
        )

    @property
    def model_name(self) -> str:
        return self.target.model_id

    def _thinking_extra_body(self, want_thinking: bool) -> dict[str, Any] | None:
        """Return the model-specific thinking override when one is safe.

        ⚠️ **不思考的模型不需要被关掉思考。** 这个参数是 DeepSeek 的方言，
        而这条协议下挂着一整类兼容服务。硅基流动上的 Qwen3-VL-Instruct 收到它
        直接 400（`current model does not support parameter enable_thinking`）——
        一个纯粹多余的参数把整次调用打死了。

        判据用档案声明的能力：没有思考能力就什么都不发，让服务端用它自己的默认。
        内置 DeepSeek 服务开启时显式发送 enabled，避免服务端默认值或模型别名让
        聊天界面的开关看起来已打开、实际仍走普通回答。其他兼容服务仍保持隐式开启，
        因为它们未必接受 DeepSeek 方言。
        """
        if not self.target.capabilities.get("thinking", False):
            return None
        if want_thinking:
            return (
                {"thinking": {"type": "enabled"}}
                if self.target.service_slug == "deepseek"
                else None
            )
        return {"thinking": {"type": "disabled"}}

    def _apply_thinking(self, request: dict[str, Any], want_thinking: bool) -> None:
        """Apply the switch and service-specific effort to one SDK request."""
        if thinking_extra_body := self._thinking_extra_body(want_thinking):
            # ``thinking`` is a DeepSeek extension and must be merged into the
            # HTTP body through the OpenAI SDK's ``extra_body`` escape hatch.
            request["extra_body"] = thinking_extra_body
        if (
            want_thinking
            and self.target.service_slug == "deepseek"
            and self.target.effort in self.target.thinking_efforts
        ):
            # Unlike ``thinking``, this is a first-class OpenAI SDK argument.
            request["reasoning_effort"] = self.target.effort

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
            self.target.thinking_default if thinking is None else thinking
        )

        for iteration in range(self.settings.max_tool_iterations):
            request: dict[str, Any] = {
                "model": self.target.model_id,
                "max_tokens": self.target.max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    *to_openai_messages(
                        working,
                        include_reasoning=self.target.service_slug == "deepseek",
                    ),
                ],
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if tools:
                request["tools"] = tools
            self._apply_thinking(request, want_thinking)

            # 记录的就是下面那个 request 本身，不另拼一份
            snapshot = (
                debug.recorder.record(
                    provider="deepseek",
                    model=self.target.model_id,
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
                # The OpenAI instrumentor may omit message content depending
                # on its version/configuration, so the enclosing application
                # span stores the exact request and assembled response too.
                with trace(
                    "llm",
                    f"openai/{self.target.model_id}",
                    provider="openai",
                    model=self.target.model_id,
                    iteration=iteration,
                ):
                    record_llm_input(request, model=self.target.model_id)
                    async for delta in self._consume(request, turn):
                        yield delta

                    assistant_content = to_content_blocks(
                        "".join(turn.text), "".join(turn.reasoning), turn.tool_calls
                    )
                    record_llm_output(
                        {
                            "content": assistant_content,
                            "stop_reason": turn.finish,
                        },
                        usage=turn.usage,
                        stop_reason=turn.finish or "",
                    )
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
        prompt: str | list[dict[str, Any]],
        max_tokens: int | None = None,
        thinking: bool = True,
    ) -> str:
        # 传 block 数组时按多模态翻译一次 —— 看图那次调用就是这么进来的。
        content = to_openai_parts(prompt) if isinstance(prompt, list) else prompt
        request: dict[str, Any] = {
            "model": self.target.model_id,
            "max_tokens": max_tokens or self.target.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        self._apply_thinking(request, thinking)
        with trace(
            "llm",
            f"openai/{self.target.model_id}",
            provider="openai",
            model=self.target.model_id,
            purpose="complete",
        ):
            record_llm_input(request, model=self.target.model_id)
            response = await self.client.chat.completions.create(**request)
            response_usage = getattr(response, "usage", None)
            usage = (
                response_usage.model_dump(exclude_none=True)
                if response_usage is not None
                else {}
            )
            record_llm_output(
                {
                    "content": response.choices[0].message.content or "",
                    "reasoning_content": getattr(
                        response.choices[0].message, "reasoning_content", ""
                    ),
                    "finish_reason": response.choices[0].finish_reason,
                },
                usage=usage,
                stop_reason=response.choices[0].finish_reason or "",
            )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            logger.warning("补全被 max_tokens 截断，产出可能不完整")
        text = (choice.message.content or "").strip()
        if not text:
            # 空产出在上层只会变成一句「不是 JSON」，看不出是模型没说话还是被截断。
            # 思考模型尤其容易把预算全花在 reasoning 上，正文一个字都不剩。
            reasoning = getattr(choice.message, "reasoning_content", None) or ""
            logger.warning(
                "补全返回空正文：finish_reason=%s thinking=%s reasoning=%d 字",
                choice.finish_reason, thinking, len(reasoning),
            )
        return text


# ---------- 标准格式（Anthropic content blocks）与 OpenAI 消息的互转 ----------


def to_openai_messages(
    messages: list[dict[str, Any]], *, include_reasoning: bool = False
) -> list[dict[str, Any]]:
    """content block 数组 → OpenAI 消息数组。

    Generic compatible services keep dropping thinking blocks because their
    dialects differ.  DeepSeek opts in: when a response contains tool calls,
    its official API requires ``reasoning_content`` on every subsequent tool
    request in that turn (and on later requests carrying tools).
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
        reasoning = "".join(
            b.get("thinking", "")
            for b in blocks
            if b.get("type") == "thinking"
        )

        if role == "user" and any(b.get("type") == "image" for b in blocks):
            # 只有带图时才换成多模态数组：纯文本消息保持字符串形状，
            # 免得给所有现存请求平白换一种写法（有些兼容服务对数组更挑剔）。
            out.append({"role": role, "content": to_openai_parts(blocks)})
            continue

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
            if include_reasoning and reasoning:
                entry["reasoning_content"] = reasoning
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
        else:
            out.append({"role": role, "content": text})
    return out


def to_openai_parts(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """content block 数组 → OpenAI 的多模态 parts 数组。

    只处理 text 和 image 两种 —— 其余（thinking / tool_use）在 OpenAI 协议里
    有各自的位置，不能混进 parts。

    图片走 data URI 而不是外链：外链意味着模型服务要能反向访问到我们，
    而这是个跑在本机的单人应用，没有公网地址可给。
    """
    parts: list[dict[str, Any]] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "text":
            text = block.get("text", "")
            if text:
                parts.append({"type": "text", "text": text})
        elif kind == "image":
            source = block.get("source", {})
            if source.get("type") != "base64":
                continue
            media_type = source.get("media_type", "image/png")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{source.get('data', '')}"
                    },
                }
            )
    return parts


def to_content_blocks(
    text: str, reasoning: str, tool_calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """OpenAI 响应 → content block 数组（落库用的标准格式）。"""
    blocks: list[dict[str, Any]] = []
    if reasoning:
        # 没有 signature —— 那是 Anthropic 特有的。DeepSeek 需要回传的是
        # ``reasoning_content`` 这段文本本身，翻译请求时再放回对应字段。
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
