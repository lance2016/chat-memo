"""面向界面的模型工具目录。

**这里一行工具定义都不写。** 分类、schema、启用状态全部从 `app/agent.py` 的
`TOOLKITS` 注册表推导，而 schema 直接问 executor 要 —— executor 是工具定义的唯一
事实来源，也正是聊天时真正交给模型的那份。

这不是洁癖。这个文件原来手写了第二份清单（记忆、时间线、知识库各一段，还硬编码了
支持的厂商列表），于是加一个工具要改两处；漏改第二处的后果是界面上少一个工具，
**而且不报错** —— 你只有在某天纳闷「这工具怎么不在列表里」时才会发现。

支持的协议也从 provider 注册表推导，不再写死 `["anthropic", "deepseek"]` ——
那份写死的清单在模型目录接入第三家厂商之后就已经是错的了。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import describe_toolkits
from app.db.session import get_session
from app.llm.factory import supported_protocols
from app.security import require_api_key
from app.settings_store import resolve_settings

router = APIRouter(
    prefix="/api/tools", tags=["tools"], dependencies=[Depends(require_api_key)]
)


@router.get("")
async def list_tools(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """返回完整目录，而不仅是当前请求里已启用的工具。

    停用的也要列出来并说明怎么开启 —— 让知识库在没挂 vault 时凭空消失，
    人会以为这个功能不存在。
    """
    settings = await resolve_settings(session)
    protocols = supported_protocols()

    tools: list[dict[str, Any]] = []
    for kit, enabled, executor in describe_toolkits(session, settings):
        for definition in _readable(executor):
            tools.append(
                {
                    **definition,
                    "category": kit.name,
                    "category_label": kit.label,
                    "enabled": enabled,
                    "request_enabled": kit.request_enabled,
                    "availability": (
                        (
                            "在聊天输入框打开后可用"
                            if kit.request_enabled
                            else "可用于所有对话"
                        )
                        if enabled
                        else kit.disabled_hint or "未启用"
                    ),
                    "protocols": protocols,
                    "native_protocol": kit.native_protocol,
                }
            )
    return {
        "total": len(tools),
        "enabled": sum(tool["enabled"] for tool in tools),
        "tools": tools,
    }


def _readable(executor: Any) -> list[dict[str, Any]]:
    """把一个 executor 的工具定义归一化成人能读的形状。

    两种格式携带的信息量不同，**优先取 OpenAI 那份**：

    - Anthropic 的原生工具是不透明的（记忆工具就是 `{"type": "memory_20250818"}`，
      没有描述也没有参数表）—— 模型对它训练过，所以定义在服务端
    - OpenAI 兼容格式必须自带 description 和 parameters，永远是可读的那份

    所以目录以 OpenAI 格式为主，Anthropic 独有的工具再补上。反过来会让记忆工具
    在界面上显示成一个没有任何说明的空条目。
    """
    entries: dict[str, dict[str, Any]] = {}
    for definition in executor.openai_definitions:
        function = definition.get("function", definition)
        entries[function["name"]] = {
            "name": function["name"],
            "description": function.get("description", ""),
            "input_schema": function.get("parameters", {}),
        }
    for definition in executor.anthropic_definitions:
        name = definition.get("name", "")
        if name and name not in entries:
            entries[name] = {
                "name": name,
                "description": definition.get("description", ""),
                "input_schema": definition.get("input_schema", {}),
            }
    return list(entries.values())
