from __future__ import annotations

import datetime as dt
import re
from typing import Any

from app.timeline.store import TimelineError, TimelineStore

TIMELINE_DESCRIPTION = """管理用户明确提到的、有具体日期或时间的个人事项。

- 明确承诺、会议、出行、生日、截止日期等，用 timeline_create 创建。
- 用户给出可直接计算的相对时长（如「一分钟后」「十分钟后」「半小时后」）时，
  这是明确时间：本轮直接创建，不要先反问确认。
- 用户说「可能、也许、暂定」时 status=pending；明确安排用 confirmed。
- 不要把愿望、泛泛计划或过去发生的事情创建成未来事项。
- 创建前如可能重复，先 timeline_list 查询；取消、完成或改期用 timeline_update 修改原事项。
- 时间必须是带 UTC offset 的 ISO 8601，结合 runtime_context 的当前日期和时区解析“明天”等相对表达。
- 只有「一会儿、晚点、找时间」这类无法计算的表达才需要追问；不要因为需要做日期加法就追问。
- 如果用户只回复「是的/对」，这是对上一条时间事项的确认；沿用上一条用户原话，不要把确认词当作新的时间依据。
- 回答用户时说明创建或修改了什么；事项本身不含关键歧义时不要额外请求确认。

**`said` 必填，写用户原话里表示时间的那几个字，原样复制**（「今天中午」「明早九点」「一分钟后」）。
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
        "description": "用户原话里表示时间的那几个字，原样复制，如「今天中午」「明早九点」「一分钟后」。用户没提时间就留空",
    },
    "ends_at": {"type": "string", "description": "可选，带时区的 ISO 8601 结束时间"},
    "all_day": {"type": "boolean"},
    "timezone": {"type": "string", "description": "IANA 时区，如 Asia/Shanghai"},
    "location": {"type": "string"},
    "recurrence": {"type": "string", "enum": ["none", "yearly"]},
    "notify": {"type": "boolean", "description": "是否到点推送提醒，默认 true"},
    "lead_minutes": {
        "type": "integer",
        "description": "提前多少分钟提醒。不填按类型取默认（会议 15 分钟、出行和生日提前一天、"
        "截止日期提前三天）。主人说「提前一小时叫我」这类要求时填这里",
    },
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


# 钟点的写法：阿拉伯数字（9点 / 18:00 / 6pm）、中文数字（九点半）、英文 o'clock，
# 以及可以直接换算的相对时长（「一分钟后」「半小时后」）。
# 「下周三」「下午」「中午」仍然没有钟点；相对时长则是明确时刻，只是需要做日期加法。
_CLOCK = re.compile(
    r"\d\s*[:：]\s*\d"           # 18:00
    r"|\d\s*(点|时|am|pm|a\.m|p\.m|o'clock)"  # 9点 / 6pm
    r"|[一二两三四五六七八九十]\s*(点|时)"      # 九点
    r"|正午|midnight|noon",
    re.IGNORECASE,
)
_RELATIVE_DURATION = re.compile(
    r"(?:\d+(?:\.\d+)?|[一二两三四五六七八九十百半]+)\s*"
    r"(?:分钟|分|小时|时|刻钟)\s*(?:后|以后|之后)"
    # 数字和单位之间必须允许空格：英文里「in 5 minutes」才是常态写法，
    # 少了这个 \s* 就只有「in an hour」这种不带数字的能通过。
    r"|(?:in|after)\s+(?:\d+(?:\.\d+)?\s*|an?\s+)?(?:minutes?|mins?|hours?|hrs?)",
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
    if _CLOCK.search(said) or _RELATIVE_DURATION.search(said):
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
