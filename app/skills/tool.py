"""skill 工具：渐进式披露的第 1、2 层。

第 0 层（名字 + 说明）在 system prompt 里，见 `app/memory/prompt.py`。
模型判断某个技能相关之后才 `skill_read` 读正文，正文点名了附带文件才 `skill_file`。

**只读，而且只出文本。** 技能包里的 `.py` / `.sh` 会被当成文本读出来，
不会被执行 —— 这套后端没有沙箱，也没有 bash 工具。工具描述里就写清楚这一点，
否则模型会自信地宣称「我已经运行了脚本」。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.skills.errors import SkillError
from app.skills.store import SkillStore

logger = logging.getLogger(__name__)

_SCHEMAS: dict[str, dict[str, Any]] = {
    "skill_read": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "技能名，取自 system prompt 里的技能清单",
            }
        },
        "required": ["name"],
    },
    "skill_file": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "技能名"},
            "path": {
                "type": "string",
                "description": "技能目录内的相对路径，如 references/api.md",
            },
            "view_range": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "可选的 [起始行, 结束行]，-1 表示到末尾",
            },
        },
        "required": ["name", "path"],
    },
}

_DESCRIPTIONS = {
    "skill_read": "读一个技能的完整说明书。system prompt 里只有技能的名字和一句话用途，"
    "判断当前任务和某个技能相关时，先用它读到具体做法再动手，不要凭名字猜。"
    "返回正文和该技能带的附带文件清单。",
    "skill_file": "读技能带的附带文件（参考资料、模板、脚本）。"
    "只在技能正文点名了某个文件时才读。脚本也只会作为文本返回 —— "
    "你没有执行环境，需要运行时把命令给主人，不要声称自己跑过。",
}

SKILL_TOOL_NAMES = frozenset(_SCHEMAS)

SKILL_TOOLS_ANTHROPIC: list[dict[str, Any]] = [
    {"name": name, "description": _DESCRIPTIONS[name], "input_schema": schema}
    for name, schema in _SCHEMAS.items()
]
SKILL_TOOLS_OPENAI: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": _DESCRIPTIONS[name],
            "parameters": schema,
        },
    }
    for name, schema in _SCHEMAS.items()
]


class SkillToolExecutor:
    """把 skill_* 调用派发到磁盘上的技能目录。

    ``allowed`` 是本次对话可见的技能名集合（已启用且 manifest 合法的那些）。
    停用的技能不在 system prompt 里，但模型可能从历史消息里记得名字 ——
    不在这里拦，「停用」就只是个界面上的装饰。
    """

    names = SKILL_TOOL_NAMES

    def __init__(self, store: SkillStore, allowed: frozenset[str] = frozenset()) -> None:
        self.store = store
        self.allowed = allowed

    @property
    def anthropic_definitions(self) -> list[dict[str, Any]]:
        return SKILL_TOOLS_ANTHROPIC

    @property
    def openai_definitions(self) -> list[dict[str, Any]]:
        return SKILL_TOOLS_OPENAI

    async def execute(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        handler = {"skill_read": self._read, "skill_file": self._file}.get(name)
        if handler is None:
            return f"未知工具 {name!r}", True

        try:
            return await handler(tool_input), False
        except SkillError as exc:
            return str(exc), True
        except Exception as exc:
            logger.exception("skill 工具执行失败: %s", name)
            return f"技能操作内部错误：{exc}", True

    async def _read(self, tool_input: dict[str, Any]) -> str:
        name = self._skill_name(tool_input)
        manifest = await asyncio.to_thread(self.store.manifest, name)
        files = await asyncio.to_thread(self.store.files, name)
        listing = (
            "\n".join(f"- {path}" for path in files)
            if files
            else "（这个技能没有附带文件）"
        )
        return (
            f"# 技能 {manifest.name}\n\n{manifest.description}\n\n"
            f"---\n\n{manifest.body}\n\n"
            f"---\n\n## 附带文件（用 skill_file 读）\n\n{listing}"
        )

    async def _file(self, tool_input: dict[str, Any]) -> str:
        name = self._skill_name(tool_input)
        path = _req(tool_input, "path")
        return await asyncio.to_thread(
            self.store.read_file, name, path, tool_input.get("view_range")
        )

    def _skill_name(self, tool_input: dict[str, Any]) -> str:
        name = _req(tool_input, "name")
        if not isinstance(name, str):
            raise SkillError(f"name 必须是字符串，收到 {type(name).__name__}")
        if name.strip() not in self.allowed:
            raise SkillError(
                f"没有名为 {name!r} 的可用技能。"
                f"当前可用：{'、'.join(sorted(self.allowed)) or '（一个都没装）'}"
            )
        return name.strip()


def _req(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise SkillError(f"缺少必填参数 {key}")
    return payload[key]
