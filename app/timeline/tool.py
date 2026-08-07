from __future__ import annotations

import datetime as dt
from typing import Any

from app.timeline.store import TimelineError, TimelineStore

TIMELINE_DESCRIPTION = """管理用户明确提到的、有具体日期或时间的个人事项。

- 明确承诺、会议、出行、生日、截止日期等，用 timeline_create 创建。
- 用户说「可能、也许、暂定」时 status=pending；明确安排用 confirmed。
- 不要把愿望、泛泛计划或过去发生的事情创建成未来事项。
- 创建前如可能重复，先 timeline_list 查询；取消、完成或改期用 timeline_update 修改原事项。
- 时间必须是带 UTC offset 的 ISO 8601，结合 runtime_context 的当前日期和时区解析“明天”等相对表达。
- 回答用户时说明创建或修改了什么；有歧义时宁可 pending，并请用户确认。"""

ITEM_PROPERTIES: dict[str, Any] = {
    "title": {"type": "string", "description": "简短明确的事项标题"},
    "details": {"type": "string", "description": "可选补充说明"},
    "kind": {"type": "string", "enum": ["todo", "event", "reminder", "birthday", "travel", "deadline", "note"]},
    "status": {"type": "string", "enum": ["pending", "confirmed", "completed", "cancelled"]},
    "starts_at": {"type": "string", "description": "带时区的 ISO 8601 开始时间"},
    "ends_at": {"type": "string", "description": "可选，带时区的 ISO 8601 结束时间"},
    "all_day": {"type": "boolean"},
    "timezone": {"type": "string", "description": "IANA 时区，如 Asia/Shanghai"},
    "location": {"type": "string"},
    "recurrence": {"type": "string", "enum": ["none", "yearly"]},
}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"name": name, "description": description, "input_schema": {"type": "object", "properties": properties, "required": required or []}}


ANTHROPIC_TOOLS = [
    _tool("timeline_list", f"{TIMELINE_DESCRIPTION}\n查询时间事项。", {"from": {"type": "string"}, "to": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "confirmed", "completed", "cancelled"]}}),
    _tool("timeline_create", f"{TIMELINE_DESCRIPTION}\n创建一条时间事项。", ITEM_PROPERTIES, ["title", "kind", "status", "starts_at"]),
    _tool("timeline_update", f"{TIMELINE_DESCRIPTION}\n更新、完成、取消或改期一条已有事项。", {"id": {"type": "integer"}, **ITEM_PROPERTIES}, ["id"]),
]

OPENAI_TOOLS = [
    {"type": "function", "function": {"name": tool["name"], "description": tool["description"], "parameters": tool["input_schema"]}}
    for tool in ANTHROPIC_TOOLS
]


def _datetime(value: Any, field: str) -> dt.datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise TimelineError(f"{field} 不是有效的 ISO 8601 时间") from exc
    if parsed.tzinfo is None:
        raise TimelineError(f"{field} 必须包含时区")
    return parsed


def _values(tool_input: dict[str, Any]) -> dict[str, Any]:
    values = {key: value for key, value in tool_input.items() if key in ITEM_PROPERTIES}
    for field in ("starts_at", "ends_at"):
        if field in values:
            values[field] = _datetime(values[field], field)
    return values


def _summary(item: Any) -> str:
    when = item.starts_at.isoformat(timespec="minutes")
    return f"#{item.id} [{item.status}] {when} {item.title}"


class TimelineToolExecutor:
    names = frozenset({"timeline_list", "timeline_create", "timeline_update"})

    def __init__(self, store: TimelineStore) -> None:
        self.store = store

    @property
    def anthropic_definitions(self) -> list[dict[str, Any]]:
        return ANTHROPIC_TOOLS

    @property
    def openai_definitions(self) -> list[dict[str, Any]]:
        return OPENAI_TOOLS

    async def execute(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        try:
            if name == "timeline_list":
                start = _datetime(tool_input.get("from"), "from")
                end = _datetime(tool_input.get("to"), "to")
                status = tool_input.get("status")
                items = await self.store.list(start=start, end=end, statuses={status} if status else None, limit=50)
                return ("\n".join(_summary(item) for item in items) or "没有匹配的时间事项"), False
            if name == "timeline_create":
                item = await self.store.create(_values(tool_input))
                await self.store.session.commit()
                return f"已创建时间事项：{_summary(item)}", False
            if name == "timeline_update":
                item_id = int(tool_input.get("id"))
                item = await self.store.update(item_id, _values(tool_input))
                await self.store.session.commit()
                return f"已更新时间事项：{_summary(item)}", False
            return f"未知工具 {name!r}", True
        except (TimelineError, TypeError, ValueError) as exc:
            await self.store.session.rollback()
            return str(exc), True
