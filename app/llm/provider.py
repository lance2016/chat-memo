from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from app.llm.events import AgentEvent


@runtime_checkable
class ToolExecutor(Protocol):
    """agent loop 用到的工具集合。记忆工具是目前唯一的实现。"""

    @property
    def anthropic_definitions(self) -> list[dict[str, Any]]:
        """Anthropic 格式的 tools 数组。"""
        ...

    @property
    def openai_definitions(self) -> list[dict[str, Any]]:
        """OpenAI 兼容格式的 tools 数组（DeepSeek 等）。"""
        ...

    async def execute(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        """执行工具，返回 (结果文本, 是否为错误)。

        错误不应抛出 —— 要作为 ``is_error`` 的 tool_result 回给模型，让它自己纠正。
        """
        ...


class LLMProvider(Protocol):
    """模型调用的抽象。换模型只加实现，不动 chat / memory 层。"""

    @property
    def model_name(self) -> str:
        """当前生效的模型 ID，用于把产出的内容标记上出处。"""
        ...

    def run(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        executor: ToolExecutor | None = None,
        thinking: bool | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """跑完整个 agent loop，流式产出事件。

        ``thinking`` 为 None 时用各 provider 的全局默认。
        """
        ...

    async def complete(
        self,
        *,
        system: str,
        prompt: str | list[dict[str, Any]],
        max_tokens: int | None = None,
        thinking: bool = True,
    ) -> str:
        """一次性补全，用于摘要这类不需要流式和工具的场景。

        ``thinking`` 默认开着 —— 每日整理是质量最敏感的活，值得让它想。
        标题这类「一句话概括」要显式关掉：思考在那里只是让用户白等。

        ``prompt`` 也接受 content block 数组，图片就是这么传进来的。
        **放宽在协议这一层而不是绕过去**：否则预描述和 image_ask 会各写一套
        「怎么把图发出去」，而两者本来就该是同一段代码。
        """
        ...
