"""OpenAI Responses API provider。

这个 provider 和 ``deepseek_provider`` 并列存在：后者使用 Chat Completions
协议，当前实现专门使用 ``client.responses.create``，适合
``openai-api-server-via-codex`` 这类 Responses 兼容服务。

应用内部仍把 Anthropic content blocks 作为统一格式，因此这里负责把历史、图片、
函数调用和流式事件翻译成 Responses API 的形状。
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
    text: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, str]] = field(default_factory=list)
    finish: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict)
        ).strip()
    return str(content)


def _parse_arguments(raw: str) -> tuple[dict[str, Any], str]:
    if not raw:
        return {}, ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"工具参数不是合法 JSON：{exc}"
    if not isinstance(parsed, dict):
        return {}, f"工具参数必须是对象，收到 {type(parsed).__name__}"
    return parsed, ""


def to_responses_tools(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chat Completions function schema → Responses function schema。"""
    tools: list[dict[str, Any]] = []
    for definition in definitions:
        function = definition.get("function", definition)
        tool: dict[str, Any] = {
            "type": "function",
            "name": function.get("name", ""),
            "description": function.get("description", ""),
            "parameters": function.get("parameters", {}),
        }
        if "strict" in function:
            tool["strict"] = bool(function["strict"])
        tools.append(tool)
    return tools


def _response_parts(
    blocks: list[dict[str, Any]], *, role: str
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    text_type = "output_text" if role == "assistant" else "input_text"
    for block in blocks:
        kind = block.get("type")
        if kind == "text" and block.get("text"):
            parts.append({"type": text_type, "text": block["text"]})
        elif kind == "image":
            source = block.get("source", {})
            if source.get("type") == "base64":
                media_type = source.get("media_type", "image/png")
                parts.append(
                    {
                        "type": "input_image",
                        "image_url": (
                            f"data:{media_type};base64,{source.get('data', '')}"
                        ),
                    }
                )
    return parts


def to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """应用内部消息 → Responses API input items。"""
    output: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        blocks = message.get("content", "")
        if isinstance(blocks, str):
            blocks = [{"type": "text", "text": blocks}]
        if not isinstance(blocks, list):
            continue

        tool_results = [block for block in blocks if block.get("type") == "tool_result"]
        if tool_results:
            output.extend(
                {
                    "type": "function_call_output",
                    "call_id": block["tool_use_id"],
                    "output": _as_text(block.get("content", "")),
                }
                for block in tool_results
            )

        tool_uses = [block for block in blocks if block.get("type") == "tool_use"]
        output.extend(
            {
                "type": "function_call",
                "call_id": block["id"],
                "name": block["name"],
                "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
            }
            for block in tool_uses
        )

        # 思考块不能按普通文本回传。Responses 的 reasoning item 需要由服务端
        # 生成的 encrypted_content；应用只保存展示用摘要，因此这里跳过它。
        parts = _response_parts(blocks, role=role)
        if parts:
            output.append({"role": role, "content": parts})
        elif not tool_results and not tool_uses:
            output.append(
                {
                    "role": role,
                    "content": [
                        {
                            "type": "output_text" if role == "assistant" else "input_text",
                            "text": "",
                        }
                    ],
                }
            )
    return output


def _usage(response: Any) -> dict[str, Any]:
    value = _get(response, "usage")
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return dict(value) if isinstance(value, dict) else {}


def _response_error(response: Any) -> str:
    error = _get(response, "error")
    if error is None:
        return "Responses API 返回失败状态"
    return str(_get(error, "message", error))


def _merge_tool_item(slots: dict[str, dict[str, str]], item: Any) -> None:
    if _get(item, "type") != "function_call":
        return
    item_id = _get(item, "id", "") or _get(item, "call_id", "")
    if not item_id:
        return
    slot = slots.setdefault(
        item_id,
        {"id": _get(item, "call_id", "") or item_id, "name": "", "arguments": ""},
    )
    slot["id"] = _get(item, "call_id", "") or slot["id"] or item_id
    slot["name"] = _get(item, "name", "") or slot["name"]
    arguments = _get(item, "arguments", "")
    if arguments:
        slot["arguments"] = arguments


def _merge_response_output(slots: dict[str, dict[str, str]], response: Any) -> None:
    for item in _get(response, "output", []) or []:
        _merge_tool_item(slots, item)


class OpenAIResponsesProvider:
    def __init__(
        self,
        settings: Settings | None = None,
        client: AsyncOpenAI | None = None,
        target: ModelTarget | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.target = target or ModelTarget.from_settings(self.settings)
        self.client = client or AsyncOpenAI(
            api_key=self.target.api_key or "not-needed",
            base_url=self.target.base_url or None,
        )

    @property
    def model_name(self) -> str:
        return self.target.model_id

    def _request(
        self,
        *,
        system: str,
        input: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        thinking: bool = True,
        stream: bool = False,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.target.model_id,
            "instructions": system,
            "input": input,
            "max_output_tokens": self.target.max_tokens,
            "store": False,
            "stream": stream,
        }
        if tools:
            request["tools"] = to_responses_tools(tools)
        if self.target.capabilities.get("thinking", False):
            request["reasoning"] = {
                "effort": self.target.effort or ("medium" if thinking else "none")
                if thinking
                else "none"
            }
        return request

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
        want_thinking = self.target.thinking_default if thinking is None else thinking

        for iteration in range(self.settings.max_tool_iterations):
            request = self._request(
                system=system,
                input=to_responses_input(working),
                tools=tools,
                thinking=want_thinking,
                stream=True,
            )
            snapshot = (
                debug.recorder.record(
                    provider="openai_responses",
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
                with trace(
                    "llm",
                    f"openai_responses/{self.target.model_id}",
                    provider="openai",
                    model=self.target.model_id,
                    iteration=iteration,
                ):
                    record_llm_input(request, model=self.target.model_id)
                    async for delta in self._consume(request, turn):
                        yield delta
                    assistant_content = _content_blocks(turn)
                    record_llm_output(
                        {"content": assistant_content, "stop_reason": turn.finish},
                        usage=turn.usage,
                        stop_reason=turn.finish or "",
                    )
            except Exception as exc:
                logger.exception("OpenAI Responses 调用失败")
                if snapshot is not None:
                    snapshot.finish(error=str(exc))
                yield Error(message=f"模型调用失败：{exc}")
                return

            if snapshot is not None:
                snapshot.finish(usage=turn.usage, stop_reason=turn.finish)
            _accumulate(total_usage, turn.usage)

            assistant_content = _content_blocks(turn)
            working.append({"role": "assistant", "content": assistant_content})
            yield AssistantTurn(
                content=assistant_content,
                usage=turn.usage,
                stop_reason=turn.finish,
            )

            if not turn.tool_calls:
                yield Done(usage=total_usage)
                return
            if executor is None:
                yield Error(message="模型请求了工具，但当前会话未启用任何工具。")
                return

            results: list[dict[str, Any]] = []
            for call in turn.tool_calls:
                tool_input, parse_error = _parse_arguments(call["arguments"])
                yield ToolUse(name=call["name"], input=tool_input)
                if parse_error:
                    text_out, is_error = parse_error, True
                else:
                    text_out, is_error = await executor.execute(call["name"], tool_input)
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
        slots: dict[str, dict[str, str]] = {}
        stream = await self.client.responses.create(**request)
        async for event in stream:
            event_type = _get(event, "type", "")
            if event_type == "response.output_text.delta":
                delta = _get(event, "delta", "")
                if delta:
                    out.text.append(delta)
                    yield TextDelta(text=delta)
            elif event_type in {
                "response.reasoning_summary_text.delta",
                "response.reasoning_text.delta",
            }:
                delta = _get(event, "delta", "")
                if delta:
                    out.reasoning.append(delta)
                    yield ThinkingDelta(text=delta)
            elif event_type == "response.function_call_arguments.delta":
                item_id = _get(event, "item_id", "")
                slot = slots.setdefault(
                    item_id,
                    {"id": item_id, "name": "", "arguments": ""},
                )
                slot["arguments"] += _get(event, "delta", "") or ""
            elif event_type == "response.function_call_arguments.done":
                item_id = _get(event, "item_id", "")
                slot = slots.setdefault(
                    item_id,
                    {"id": item_id, "name": "", "arguments": ""},
                )
                slot["name"] = _get(event, "name", "") or slot["name"]
                slot["arguments"] = _get(event, "arguments", "") or slot["arguments"]
            elif event_type in {
                "response.output_item.added",
                "response.output_item.done",
            }:
                _merge_tool_item(slots, _get(event, "item"))
            elif event_type == "response.completed":
                response = _get(event, "response")
                out.usage = _usage(response)
                _merge_response_output(slots, response)
                out.finish = "stop"
            elif event_type == "response.incomplete":
                response = _get(event, "response")
                out.usage = _usage(response)
                _merge_response_output(slots, response)
                out.finish = "length"
            elif event_type in {"response.failed", "error"}:
                response = _get(event, "response", event)
                raise RuntimeError(_response_error(response))

        out.tool_calls = list(slots.values())
        if out.tool_calls and out.finish == "stop":
            out.finish = "tool_calls"
        if out.finish is None:
            out.finish = "tool_calls" if out.tool_calls else "stop"

    async def complete(
        self,
        *,
        system: str,
        prompt: str | list[dict[str, Any]],
        max_tokens: int | None = None,
        thinking: bool = True,
    ) -> str:
        input_value: str | list[dict[str, Any]] = (
            prompt
            if isinstance(prompt, str)
            else to_responses_input([{"role": "user", "content": prompt}])
        )
        request = self._request(
            system=system,
            input=input_value,
            thinking=thinking,
        )
        if max_tokens is not None:
            request["max_output_tokens"] = max_tokens
        request["stream"] = False
        with trace(
            "llm",
            f"openai_responses/{self.target.model_id}",
            provider="openai",
            model=self.target.model_id,
            purpose="complete",
        ):
            record_llm_input(request, model=self.target.model_id)
            response = await self.client.responses.create(**request)
            if _get(response, "status") == "failed":
                raise RuntimeError(_response_error(response))
            text = _get(response, "output_text", "") or _response_output_text(response)
            usage = _usage(response)
            record_llm_output(
                {"content": text}, usage=usage, stop_reason=_get(response, "status", "")
            )
        return text.strip()


def _response_output_text(response: Any) -> str:
    pieces: list[str] = []
    for item in _get(response, "output", []) or []:
        if _get(item, "type") != "message":
            continue
        for content in _get(item, "content", []) or []:
            if _get(content, "type") == "output_text":
                pieces.append(_get(content, "text", ""))
    return "".join(pieces)


def _content_blocks(turn: _Turn) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if turn.reasoning:
        blocks.append({"type": "thinking", "thinking": "".join(turn.reasoning)})
    if turn.text:
        blocks.append({"type": "text", "text": "".join(turn.text)})
    for call in turn.tool_calls:
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


def _accumulate(total: dict[str, int], usage: dict[str, Any]) -> None:
    for key, value in usage.items():
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"
