"""把多个 ToolExecutor 拼成一个。

agent loop 只接受一个 executor；记忆和知识库各自独立实现，这里按工具名路由。
child 需要一个 ``names`` 属性声明自己处理哪些工具名。
"""

from __future__ import annotations

from typing import Any

from app.llm.provider import ToolExecutor


class CompositeExecutor:
    def __init__(self, *executors: ToolExecutor) -> None:
        self.executors = executors

    @property
    def anthropic_definitions(self) -> list[dict[str, Any]]:
        return [d for e in self.executors for d in e.anthropic_definitions]

    @property
    def openai_definitions(self) -> list[dict[str, Any]]:
        return [d for e in self.executors for d in e.openai_definitions]

    async def execute(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        for executor in self.executors:
            if name in getattr(executor, "names", ()):
                return await executor.execute(name, tool_input)
        return f"未知工具 {name!r}", True
