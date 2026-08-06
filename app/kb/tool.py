"""kb 工具：定义与派发。

和 memory 工具的两个关键区别：这是**只读**的自定义工具（没有 Anthropic 原生实现，
两家都用手写 schema），路径用 vault 相对路径而非 /memories 前缀。

每次调用落一行 ``kb_reads`` 埋点 —— 知识库将来要不要开写权限，靠这张表的数据说话。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KbRead
from app.kb.errors import KbToolError
from app.kb.paths import normalize_rel_path
from app.kb.search import Hit, backlinks, list_dir, read_note, search

logger = logging.getLogger(__name__)

_SCHEMAS: dict[str, dict[str, Any]] = {
    "kb_search": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索词，子串匹配文件名和内容；tag:#标签 只搜标签",
            },
            "path_prefix": {
                "type": "string",
                "description": "可选，只搜这个子目录，如 Projects",
            },
            "limit": {"type": "integer", "description": "最多返回几条，默认 20"},
        },
        "required": ["query"],
    },
    "kb_read": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "vault 相对路径，如 Projects/chat-memo.md",
            },
            "view_range": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "可选的 [起始行, 结束行]，-1 表示到末尾",
            },
        },
        "required": ["path"],
    },
    "kb_list": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目录相对路径，省略或空串表示根目录",
            }
        },
    },
    "kb_backlinks": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "笔记的相对路径"}},
        "required": ["path"],
    },
}

_DESCRIPTIONS = {
    "kb_search": "搜索主人的 Obsidian 知识库（只读）。找任何笔记内容都先用它，"
    "不要逐层 kb_list。返回相对路径、命中行和修改日期，细节用 kb_read 读。",
    "kb_read": "读知识库里的一篇笔记，带行号。笔记是主人亲手写的，引用时注明路径。"
    "知识库不可写，值得沉淀的结论写进你自己的记忆（memory 工具）。",
    "kb_list": "列知识库某个目录的内容。浏览用，找东西优先 kb_search。",
    "kb_backlinks": "查哪些笔记通过 [[双链]] 引用了给定笔记，沿链接网络找相关内容。",
}

KB_TOOL_NAMES = frozenset(_SCHEMAS)

KB_TOOLS_ANTHROPIC: list[dict[str, Any]] = [
    {"name": name, "description": _DESCRIPTIONS[name], "input_schema": schema}
    for name, schema in _SCHEMAS.items()
]
KB_TOOLS_OPENAI: list[dict[str, Any]] = [
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


class KbToolExecutor:
    """把 kb_* 工具调用派发到 vault 扫描函数。

    扫描是同步文件 IO，统一走 asyncio.to_thread，不阻塞事件循环。
    所有失败转成 ``is_error`` 的结果文本回给模型，让它自己纠正 —— 同 memory 工具的纪律。
    """

    names = KB_TOOL_NAMES

    def __init__(
        self,
        session: AsyncSession,
        vault_root: str,
        conversation_id: int | None = None,
    ) -> None:
        self.session = session
        self.vault_root = vault_root
        self.conversation_id = conversation_id

    @property
    def anthropic_definitions(self) -> list[dict[str, Any]]:
        return KB_TOOLS_ANTHROPIC

    @property
    def openai_definitions(self) -> list[dict[str, Any]]:
        return KB_TOOLS_OPENAI

    async def execute(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        handler = {
            "kb_search": self._search,
            "kb_read": self._read,
            "kb_list": self._list,
            "kb_backlinks": self._backlinks,
        }.get(name)
        if handler is None:
            return f"未知工具 {name!r}", True

        try:
            return await handler(tool_input), False
        except KbToolError as exc:
            return str(exc), True
        except Exception as exc:
            logger.exception("kb 工具执行失败: %s", name)
            return f"知识库操作内部错误：{exc}", True

    # ---------- 四个命令 ----------

    async def _search(self, tool_input: dict[str, Any]) -> str:
        query = _req(tool_input, "query")
        if not isinstance(query, str):
            raise KbToolError(f"query 必须是字符串，收到 {type(query).__name__}")
        prefix = normalize_rel_path(tool_input.get("path_prefix") or "")
        limit = _int(tool_input.get("limit"), default=20)

        hits = await asyncio.to_thread(search, self.vault_root, query, prefix, limit)
        # search 的 found=False 是内容缺口信号：模型想找但库里没有
        self._record("search", query, found=bool(hits))
        if not hits:
            return f"没有搜到 {query!r}。可以换个关键词，或用 kb_list 看看目录结构。"
        return f"搜到 {len(hits)} 条：\n{_render_hits(hits)}"

    async def _read(self, tool_input: dict[str, Any]) -> str:
        path = normalize_rel_path(_req(tool_input, "path"))
        try:
            text = await asyncio.to_thread(
                read_note, self.vault_root, path, tool_input.get("view_range")
            )
        except KbToolError:
            self._record("read", path, found=False)
            raise
        self._record("read", path, found=True)
        return text

    async def _list(self, tool_input: dict[str, Any]) -> str:
        path = normalize_rel_path(tool_input.get("path") or "")
        try:
            listing = await asyncio.to_thread(list_dir, self.vault_root, path)
        except KbToolError:
            self._record("list", path or "/", found=False)
            raise
        self._record("list", path or "/", found=True)
        return listing

    async def _backlinks(self, tool_input: dict[str, Any]) -> str:
        path = normalize_rel_path(_req(tool_input, "path"))
        try:
            hits = await asyncio.to_thread(backlinks, self.vault_root, path)
        except KbToolError:
            self._record("backlinks", path, found=False)
            raise
        self._record("backlinks", path, found=bool(hits))
        if not hits:
            return f"没有笔记链接到 {path}。"
        return f"{len(hits)} 篇笔记链接到 {path}：\n{_render_hits(hits)}"

    def _record(self, command: str, target: str, found: bool) -> None:
        self.session.add(
            KbRead(
                command=command,
                target=target[:512],
                found=found,
                conversation_id=self.conversation_id,
            )
        )


def _render_hits(hits: list[Hit]) -> str:
    return "\n".join(
        f"- {h.path}  ({dt.date.fromtimestamp(h.mtime).isoformat()})\n  {h.snippet}"
        for h in hits
    )


def _req(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise KbToolError(f"缺少必填参数 {key}")
    return payload[key]


def _int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise KbToolError(f"limit 必须是整数，收到 {value!r}")
    return value
