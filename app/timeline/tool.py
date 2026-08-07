from __future__ import annotations

import datetime as dt
import re
from typing import Any

from app.timeline.store import TimelineError, TimelineStore

TIMELINE_DESCRIPTION = """管理用户明确提到的、有具体日期或时间的个人事项。

- 明确承诺、会议、出行、生日、截止日期等，用 timeline_create 创建。
- 用户说「可能、也许、暂定」时 status=pending；明确安排用 confirmed。
- 不要把愿望、泛泛计划或过去发生的事情创建成未来事项。
- 创建前如可能重复，先 timeline_list 查询；取消、完成或改期用 timeline_update 修改原事项。
- 时间必须是带 UTC offset 的 ISO 8601，结合 runtime_context 的当前日期和时区解析“明天”等相对表达。
- 回答用户时说明创建或修改了什么；有歧义时宁可 pending，并请用户确认。

**`said` 必填，写用户原话里表示时间的那几个字，原样复制**（「今天中午」「明早九点」）。
用户没说到时间就留空。这个字段会被校验：**说的是「中午」「晚点」「下午」这类没有钟点的
话时，工具会拒绝创建，你要先问清楚大概几点，而不是自己挑一个时间填进去。**
真的不需要具体时间（整天有效的待办、生日）就设 all_day=true。"""

ITEM_PROPERTIES: dict[str, Any] = {
    "title": {"type": "string", "description": "简短明确的事项标题"},
    "details": {"type": "string", "description": "可选补充说明"},
    "kind": {"type": "string", "enum": ["todo", "event", "reminder", "birthday", "travel", "deadline", "note"]},
    "status": {"type": "string", "enum": ["pending", "confirmed", "completed", "cancelled"]},
    "starts_at": {"type": "string", "description": "带时区的 ISO 8601 开始时间"},
    "said": {
        "type": "string",
        "description": "用户原话里表示时间的那几个字，原样复制，如「今天中午」「明早九点」。用户没提时间就留空",
    },
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
    _tool("timeline_create", f"{TIMELINE_DESCRIPTION}\n创建一条时间事项。", ITEM_PROPERTIES, ["title", "kind", "status", "starts_at", "said"]),
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


# 钟点的写法：阿拉伯数字（9点 / 18:00 / 6pm）、中文数字（九点半）、以及英文 o'clock。
# 判据是「有没有钟点」而不是「有没有『中午』这类模糊词」—— 模糊词列不全，
# 而缺钟点是所有模糊表达的共同特征：「中午」「晚点」「回头」「下周三」都缺。
_CLOCK = re.compile(
    r"\d\s*[:：]\s*\d"           # 18:00
    r"|\d\s*(点|时|am|pm|a\.m|p\.m|o'clock)"  # 9点 / 6pm
    r"|[一二两三四五六七八九十]\s*(点|时)"      # 九点
    r"|正午|midnight|noon",
    re.IGNORECASE,
)


def _require_clock(tool_input: dict[str, Any]) -> None:
    """没有钟点就不许落一个精确时间。

    工具原来只要求 starts_at，模型没有任何办法表达「知道是哪天、不知道几点」，
    于是「今天中午」会被填成一个凭空捏造的 11:20 并标成 confirmed。提示词里
    「有歧义时宁可 pending」拦不住 —— 已经实测被无视了，所以改成硬校验：
    拒绝之后模型拿到的是 is_error 的 tool_result，自然会回头问用户。
    """
    if tool_input.get("all_day"):
        return
    said = str(tool_input.get("said") or "").strip()
    if not said:
        raise TimelineError(
            "缺少 said。把用户原话里表示时间的那几个字填进来；"
            "用户没提到时间就不要凭空定一个，先问他。"
        )
    if _CLOCK.search(said):
        return
    raise TimelineError(
        f"「{said}」没有具体钟点，不能创建带精确时间的事项 —— 不要自己挑一个。"
        "先问用户大概几点，拿到答复再创建。"
        "如果他说随便/你定，或者这件事整天有效，就用 all_day=true。"
    )


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
                _require_clock(tool_input)
                item = await self.store.create(_values(tool_input))
                await self.store.session.commit()
                return f"已创建时间事项：{_summary(item)}", False
            if name == "timeline_update":
                # 改期同样不能改成一个猜出来的时间。只在真的动了 starts_at 时校验 ——
                # 标记完成、改标题这类更新不该被时间规则挡住。
                if "starts_at" in tool_input:
                    _require_clock(tool_input)
                item_id = int(tool_input.get("id"))
                item = await self.store.update(item_id, _values(tool_input))
                await self.store.session.commit()
                return f"已更新时间事项：{_summary(item)}", False
            return f"未知工具 {name!r}", True
        except (TimelineError, TypeError, ValueError) as exc:
            await self.store.session.rollback()
            return str(exc), True
