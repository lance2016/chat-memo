from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic

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


class AnthropicProvider:
    def __init__(
        self, settings: Settings | None = None, client: AsyncAnthropic | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or AsyncAnthropic(api_key=self.settings.anthropic_api_key)

    def _request_kwargs(self, system: str, thinking: bool = True) -> dict[str, Any]:
        effort = self.settings.effort
        if not thinking and effort in {"xhigh", "max"}:
            # Opus 5 上「关闭思考」只在 effort <= high 时被接受，否则 400。
            effort = "high"
        return {
            "model": self.settings.model,
            "max_tokens": self.settings.max_tokens,
            # system 是稳定前缀（人格 + 记忆索引），在这里打缓存断点。
            # 绝不能往 system 里插时间戳/会话 ID —— 缓存是前缀匹配，一变就全失效。
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            # display 默认是 omitted，不显式设 summarized 的话 thinking 块文本为空，
            # 前端看到的就是一段长时间的静默。
            "thinking": (
                {"type": "adaptive", "display": "summarized"}
                if thinking
                else {"type": "disabled"}
            ),
            "output_config": {"effort": effort},
        }

    async def run(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        executor: ToolExecutor | None = None,
        thinking: bool | None = None,
    ) -> AsyncIterator[AgentEvent]:
        want_thinking = True if thinking is None else thinking
        working = list(messages)
        tools = executor.anthropic_definitions if executor is not None else []
        total_usage: dict[str, int] = {}

        for iteration in range(self.settings.max_tool_iterations):
            # 组好再发，**调试记录的就是这一个 dict** —— 另拼一份给调试看，
            # 迟早和真正发出去的那份不一致，那样的调试信息比没有更糟。
            payload: dict[str, Any] = {
                # 传快照：本轮之后还会往 working 里追加，不能让调用方持有活引用。
                "messages": strip_unsigned_thinking(working),
                "tools": tools,
                **self._request_kwargs(system, want_thinking),
            }
            snapshot = (
                debug.recorder.record(
                    provider="anthropic",
                    model=self.settings.model,
                    payload=payload,
                    iteration=iteration,
                )
                if self.settings.debug_prompts
                else None
            )
            if snapshot is not None:
                log_request(logger, snapshot)

            try:
                final = None
                async with self.client.messages.stream(**payload) as stream:
                    async for event in stream:
                        if event.type != "content_block_delta":
                            continue
                        delta = event.delta
                        if delta.type == "thinking_delta":
                            yield ThinkingDelta(text=delta.thinking)
                        elif delta.type == "text_delta":
                            yield TextDelta(text=delta.text)
                    final = await stream.get_final_message()
            except Exception as exc:  # 网络/API 错误，转成事件而不是打断 SSE
                logger.exception("模型调用失败")
                if snapshot is not None:
                    snapshot.finish(error=str(exc))
                yield Error(message=f"模型调用失败：{exc}")
                return

            usage = final.usage.model_dump(exclude_none=True)
            if snapshot is not None:
                snapshot.finish(usage=usage, stop_reason=final.stop_reason)
            _accumulate(total_usage, usage)

            # 必须原样保留整个 content 数组（含 thinking 块及其签名），
            # 只抽 text 会在下一轮触发 400。
            assistant_content = [
                block.model_dump(exclude_none=True) for block in final.content
            ]
            working.append({"role": "assistant", "content": assistant_content})
            yield AssistantTurn(
                content=assistant_content,
                usage=usage,
                stop_reason=final.stop_reason,
            )

            if final.stop_reason == "refusal":
                yield Error(message="模型基于安全策略拒绝了这次请求。")
                return

            if final.stop_reason != "tool_use":
                yield Done(usage=total_usage)
                return

            if executor is None:
                yield Error(message="模型请求了工具，但当前会话未启用任何工具。")
                return

            results: list[dict[str, Any]] = []
            for block in final.content:
                if block.type != "tool_use":
                    continue
                tool_input = dict(block.input) if isinstance(block.input, dict) else {}
                yield ToolUse(name=block.name, input=tool_input)

                text, is_error = await executor.execute(block.name, tool_input)
                yield ToolResult(
                    name=block.name, ok=not is_error, summary=_clip(text, 200)
                )

                result: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": text,
                }
                if is_error:
                    result["is_error"] = True
                results.append(result)

            working.append({"role": "user", "content": results})
            yield ToolResultTurn(content=results)

        yield Error(
            message=f"超过单次请求的最大工具轮次（{self.settings.max_tool_iterations}）。"
        )

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
        thinking: bool = True,
    ) -> str:
        """非流式补全，用于摘要/整理这类后台任务。

        走 stream + get_final_message 而不是裸 create：max_tokens 较大时
        非流式请求会撞上 SDK 的 HTTP 超时。
        """
        async with self.client.messages.stream(
            model=self.settings.model,
            max_tokens=max_tokens or self.settings.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"} if thinking else {"type": "disabled"},
        ) as stream:
            message = await stream.get_final_message()

        text = "\n".join(b.text for b in message.content if b.type == "text").strip()
        if message.stop_reason == "max_tokens":
            # thinking 也算进 max_tokens，预算太紧会只剩思考、正文被截断。
            logger.warning(
                "补全被 max_tokens 截断，产出可能不完整（已产出 %d 字符）", len(text)
            )
        return text


def strip_unsigned_thinking(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """丢掉没有 signature 的 thinking 块。

    Anthropic 要求 thinking 块带签名才能回传，而这些块可能来自别处：
    DeepSeek 生成的 thinking 没有 signature（那是 Anthropic 特有的），
    中断兜底保存的半截回答也不带。同一个会话换到 Claude 时，
    这些块会让整个请求 400 —— 而且历史里一直留着，等于会话永久不可用。

    丢掉它们是安全的：思考内容本来就是临时的，丢了不影响对话语义。
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            out.append(message)
            continue

        kept = [
            block
            for block in content
            if not (
                isinstance(block, dict)
                and block.get("type") == "thinking"
                and not block.get("signature")
            )
        ]
        if len(kept) == len(content):
            out.append(message)
        elif kept:
            out.append({**message, "content": kept})
        # 整条消息只有无签名 thinking 时直接丢弃，不留空 content（API 会拒绝）
    return out


def _accumulate(total: dict[str, int], usage: dict[str, Any]) -> None:
    for key, value in usage.items():
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"
