"""什么时候提醒：把事项的开始时间换算成触发时刻。

这里是纯函数，不碰数据库也不碰配置对象以外的东西 —— 提醒时刻的算法值得单独测，
不该埋在 store 的校验流程里。
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 按类型给的默认提前量（分钟）。判据是「提前多久知道才来得及做点什么」：
# 会议提前一刻钟够走过去，出行和生日要提前一天才能准备，截止日期提前三天才救得回来。
# note 是纯记录，压根不该响。
DEFAULT_LEAD_MINUTES: dict[str, int | None] = {
    "event": 15,
    "reminder": 0,
    "todo": 0,
    "deadline": 3 * 24 * 60,
    "travel": 24 * 60,
    "birthday": 24 * 60,
    "note": None,
}

# 提醒不适用的状态：做完和取消的事项不该再响。
SILENT_STATUSES = frozenset({"completed", "cancelled"})


def resolve_lead_minutes(kind: str, explicit: int | None, fallback: int) -> int | None:
    """显式设置 > 按 kind 的默认 > 全局默认。

    ``None`` 表示这个类型默认不提醒（note）。显式传 0 是「准点提醒」，
    和「不提醒」是两件事 —— 所以判空要用 ``is None``，不能用真值判断。
    """
    if explicit is not None:
        return max(0, explicit)
    if kind in DEFAULT_LEAD_MINUTES:
        default = DEFAULT_LEAD_MINUTES[kind]
        return default if default is None else max(0, default)
    return max(0, fallback)


def _zone(name: str) -> dt.tzinfo | None:
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        # 时区名是模型填的，可能是「北京时间」这种非 IANA 写法。
        # 拿不到就退回事项自己的 offset，不值得为它整条失败。
        return None


def base_moment(
    starts_at: dt.datetime, *, all_day: bool, timezone: str, all_day_hour: int
) -> dt.datetime:
    """提醒基准点。

    全天事项的 starts_at 通常是当地 00:00，直接按它减提前量会在半夜响。
    所以全天事项一律换算成当天的 ``all_day_hour`` 点，再减提前量 ——
    「明天生日」于是变成今天早上九点提醒，而不是今天凌晨零点。
    """
    if not all_day:
        return starts_at
    local = starts_at.astimezone(_zone(timezone) or starts_at.tzinfo)
    return local.replace(hour=all_day_hour, minute=0, second=0, microsecond=0)


def compute_remind_at(
    *,
    starts_at: dt.datetime,
    kind: str,
    status: str,
    all_day: bool,
    timezone: str,
    notify: bool,
    lead_minutes: int | None,
    default_lead_minutes: int,
    all_day_hour: int,
) -> dt.datetime | None:
    """算出该在什么时刻提醒，不该提醒时返回 None。

    返回 None 的四种情况都要显式落库成 NULL —— ticker 查的就是 ``remind_at IS NOT NULL``，
    留着旧值会让一件已完成的事继续响。
    """
    if not notify or status in SILENT_STATUSES:
        return None
    lead = resolve_lead_minutes(kind, lead_minutes, default_lead_minutes)
    if lead is None:
        return None
    base = base_moment(
        starts_at, all_day=all_day, timezone=timezone, all_day_hour=all_day_hour
    )
    return base - dt.timedelta(minutes=lead)
