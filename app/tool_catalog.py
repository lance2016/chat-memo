"""面向界面的模型工具目录。

聊天执行器仍然是工具定义的唯一事实来源；这里仅把不同 provider 的形状
归一化，方便人查看和判断还需要补哪些能力。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.kb.tool import KB_TOOLS_ANTHROPIC
from app.memory.tool import (
    MEMORY_TOOL_DESCRIPTION,
    MEMORY_TOOL_PARAMETERS,
)
from app.security import require_api_key
from app.settings_store import resolve_settings
from app.timeline.tool import ANTHROPIC_TOOLS as TIMELINE_TOOLS

router = APIRouter(
    prefix="/api/tools", tags=["tools"], dependencies=[Depends(require_api_key)]
)


def _entry(
    definition: dict[str, Any],
    *,
    category: str,
    category_label: str,
    enabled: bool = True,
    availability: str = "可用于所有对话",
    native_provider: str | None = None,
) -> dict[str, Any]:
    return {
        "name": definition["name"],
        "description": definition["description"],
        "input_schema": definition["input_schema"],
        "category": category,
        "category_label": category_label,
        "enabled": enabled,
        "availability": availability,
        "providers": ["anthropic", "deepseek"],
        "native_provider": native_provider,
    }


@router.get("")
async def list_tools(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """返回完整目录，而不仅是当前请求里已启用的工具。"""
    settings = await resolve_settings(session)
    kb_enabled = bool(settings.vault_path)

    memory = _entry(
        {
            "name": "memory",
            "description": MEMORY_TOOL_DESCRIPTION,
            "input_schema": MEMORY_TOOL_PARAMETERS,
        },
        category="memory",
        category_label="长期记忆",
        native_provider="anthropic",
    )
    timeline = [
        _entry(tool, category="timeline", category_label="时间线")
        for tool in TIMELINE_TOOLS
    ]
    knowledge = [
        _entry(
            tool,
            category="knowledge",
            category_label="知识库",
            enabled=kb_enabled,
            availability=(
                "只读知识库已挂载"
                if kb_enabled
                else "未启用：设置 VAULT_PATH 并重启后端"
            ),
        )
        for tool in KB_TOOLS_ANTHROPIC
    ]
    tools = [memory, *timeline, *knowledge]
    return {
        "total": len(tools),
        "enabled": sum(tool["enabled"] for tool in tools),
        "tools": tools,
    }
