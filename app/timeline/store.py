from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import TimelineItem
from app.notify.schedule import compute_remind_at
from app.timeutils import aware

KINDS = frozenset({"todo", "event", "reminder", "birthday", "travel", "deadline", "note"})
STATUSES = frozenset({"pending", "confirmed", "completed", "cancelled"})
RECURRENCES = frozenset({"none", "yearly"})

# 还没做完的。逾期查询和年度展开都只关心这些。
LIVE_STATUSES = ("pending", "confirmed")


class TimelineError(ValueError):
    pass


def project_yearly(item: TimelineItem, year: int) -> TimelineItem:
    """把一条每年重复的事项投影到指定年份。

    返回**未加入 session** 的副本 —— 直接改原对象的 starts_at 会在 flush 时把
    投影结果写回数据库，等于每查一次月历就把生日改到当年。

    id 保持不变：前端要靠它完成、删除和跳转，指向的本来就是同一条记录。
    同一个查询区间内一个 id 最多出现一次，做 React key 是安全的。
    """
    start = item.starts_at
    try:
        moved = start.replace(year=year)
    except ValueError:
        # 2 月 29 日。非闰年落到 2 月 28 日，而不是消失一整年。
        moved = start.replace(year=year, day=28)
    shifted = moved - start

    clone = TimelineItem(
        title=item.title,
        details=item.details,
        kind=item.kind,
        status=item.status,
        starts_at=moved,
        said=item.said,
        ends_at=item.ends_at + shifted if item.ends_at is not None else None,
        all_day=item.all_day,
        timezone=item.timezone,
        location=item.location,
        recurrence=item.recurrence,
        actor=item.actor,
        source_conversation_id=item.source_conversation_id,
        source_message_id=item.source_message_id,
        notify=item.notify,
        lead_minutes=item.lead_minutes,
        remind_at=item.remind_at + shifted if item.remind_at is not None else None,
        snoozed_until=item.snoozed_until,
    )
    clone.id = item.id
    clone.created_at = item.created_at
    clone.updated_at = item.updated_at
    return clone


class TimelineStore:
    def __init__(
        self,
        session: AsyncSession,
        *,
        actor: str,
        conversation_id: int | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.actor = actor
        self.conversation_id = conversation_id
        # 提醒时刻要用到 notify_* 那几个配置。不传就退回运行时默认 ——
        # 调用方拿得到合并后的 settings 时应该传进来。
        self.settings = settings or get_settings()

    async def get(self, item_id: int) -> TimelineItem:
        item = await self.session.get(TimelineItem, item_id)
        if item is None:
            raise TimelineError("时间事项不存在")
        return item

    async def list(
        self,
        *,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
        statuses: set[str] | None = None,
        limit: int = 200,
        include_overdue: bool = False,
    ) -> list[TimelineItem]:
        """区间内的事项。

        ``include_overdue`` 会额外带上区间开始之前、但还没做完的事项。没有这个开关的话，
        「今天」视图看不到昨天没勾掉的事 —— 事项会静默沉底，只有翻月历才找得回来。
        """
        if statuses:
            unknown = statuses - STATUSES
            if unknown:
                raise TimelineError(f"未知状态：{', '.join(sorted(unknown))}")

        capped = min(limit, 500)
        stmt = select(TimelineItem).order_by(TimelineItem.starts_at, TimelineItem.id)
        window = []
        if start is not None:
            window.append(TimelineItem.starts_at >= start)
        if end is not None:
            window.append(TimelineItem.starts_at < end)
        if window and include_overdue and start is not None:
            overdue = [TimelineItem.starts_at < start, TimelineItem.status.in_(LIVE_STATUSES)]
            stmt = stmt.where(or_(and_(*window), and_(*overdue)))
        else:
            for condition in window:
                stmt = stmt.where(condition)
        if statuses:
            stmt = stmt.where(TimelineItem.status.in_(statuses))

        items = list((await self.session.execute(stmt.limit(capped))).scalars())
        if start is None or end is None:
            return items
        return sorted(
            items + await self._yearly_occurrences(start, end, statuses, items),
            key=lambda item: (aware(item.starts_at), item.id),
        )[:capped]

    async def _yearly_occurrences(
        self,
        start: dt.datetime,
        end: dt.datetime,
        statuses: set[str] | None,
        already: list[TimelineItem],
    ) -> list[TimelineItem]:
        """把每年重复的事项展开到查询区间里。

        没有这一步，2026 年记的生日在 2027 年的月历里什么都不显示 —— ``recurrence``
        字段过去只用来在卡片上印「每年重复」四个字，查询是纯 ``starts_at`` 范围过滤。

        在 Python 里展开而不是写 SQL：单人使用，每年重复的事项就是几个生日，
        为它写一段跨方言的日期函数不划算。
        """
        stmt = select(TimelineItem).where(TimelineItem.recurrence == "yearly")
        if statuses:
            stmt = stmt.where(TimelineItem.status.in_(statuses))
        recurring = list((await self.session.execute(stmt)).scalars())
        if not recurring:
            return []

        # 按 (id, 时刻) 去重：基准那一年本来就在区间里时已经查出来了，不能再补一条。
        seen = {(item.id, item.starts_at) for item in already}
        occurrences = []
        for item in recurring:
            for year in range(start.year, end.year + 1):
                occurrence = project_yearly(item, year)
                if not (start <= aware(occurrence.starts_at) < end):
                    continue
                if (occurrence.id, occurrence.starts_at) in seen:
                    continue
                seen.add((occurrence.id, occurrence.starts_at))
                occurrences.append(occurrence)
        return occurrences

    async def create(self, values: dict[str, Any]) -> TimelineItem:
        data = self._validated(values, partial=False)
        item = TimelineItem(
            **data,
            actor=self.actor,
            source_conversation_id=self.conversation_id,
        )
        item.remind_at = self._remind_at(item)
        self.session.add(item)
        await self.session.flush()
        return item

    async def update(self, item_id: int, values: dict[str, Any]) -> TimelineItem:
        item = await self.get(item_id)
        changes = self._validated(values, partial=True)
        for key, value in changes.items():
            setattr(item, key, value)
        if item.ends_at is not None and item.ends_at < item.starts_at:
            raise TimelineError("结束时间不能早于开始时间")
        # 改期就是一条新提醒，之前的「稍后再说」不该继续压着它。
        if "starts_at" in changes:
            item.snoozed_until = None
        item.remind_at = self._remind_at(item)
        item.actor = self.actor
        await self.session.flush()
        return item

    async def snooze(self, item_id: int, minutes: int) -> TimelineItem:
        if minutes < 0 or minutes > 7 * 24 * 60:
            raise TimelineError("推迟时长必须在 0 分钟到 7 天之间")
        item = await self.get(item_id)
        if item.status in ("completed", "cancelled"):
            raise TimelineError("已完成或已取消的事项不需要推迟")
        now = dt.datetime.now(dt.UTC)
        item.snoozed_until = now + dt.timedelta(minutes=minutes)
        if minutes == 0:
            # 「立即提醒」必须把实际提醒时刻也拉到现在；否则未来事项仍会被 remind_at 挡住。
            # 用户明确选择了立即提醒时，顺便恢复这条事项的通知开关。
            item.notify = True
            item.remind_at = now
        await self.session.flush()
        return item

    async def delete(self, item_id: int) -> None:
        item = await self.get(item_id)
        await self.session.delete(item)
        await self.session.flush()

    def _remind_at(self, item: TimelineItem) -> dt.datetime | None:
        return compute_remind_at(
            starts_at=item.starts_at,
            kind=item.kind,
            status=item.status,
            all_day=item.all_day,
            timezone=item.timezone,
            notify=item.notify,
            lead_minutes=item.lead_minutes,
            default_lead_minutes=self.settings.notify_default_lead_minutes,
            all_day_hour=self.settings.notify_all_day_hour,
        )

    def _validated(self, values: dict[str, Any], *, partial: bool) -> dict[str, Any]:
        allowed = {"title", "details", "kind", "status", "starts_at", "said", "ends_at", "all_day", "timezone", "location", "recurrence", "source_message_id", "notify", "lead_minutes"}
        data = {key: value for key, value in values.items() if key in allowed}
        if not partial and not str(data.get("title") or "").strip():
            raise TimelineError("标题不能为空")
        if not partial and data.get("starts_at") is None:
            raise TimelineError("开始时间不能为空")
        if "title" in data:
            data["title"] = str(data["title"]).strip()
            if not data["title"] or len(data["title"]) > 240:
                raise TimelineError("标题长度必须为 1～240 个字符")
        if "said" in data:
            # 只是依据，超长截断即可 —— 不值得为它让整条创建失败。
            data["said"] = str(data["said"] or "").strip()[:120]
        for key in ("details", "location", "timezone"):
            if key in data:
                data[key] = str(data[key] or "").strip()
        if data.get("kind", "todo") not in KINDS:
            raise TimelineError("不支持的事项类型")
        if data.get("status", "confirmed") not in STATUSES:
            raise TimelineError("不支持的事项状态")
        if data.get("recurrence", "none") not in RECURRENCES:
            raise TimelineError("不支持的重复规则")
        if data.get("lead_minutes") is not None:
            lead = data["lead_minutes"]
            if isinstance(lead, bool) or not isinstance(lead, int):
                raise TimelineError("提前量必须是整数分钟")
            if not 0 <= lead <= 30 * 24 * 60:
                raise TimelineError("提前量必须在 0 分钟到 30 天之间")
        for key in ("starts_at", "ends_at"):
            if key in data and data[key] is not None and data[key].tzinfo is None:
                raise TimelineError(f"{key} 必须包含时区")
        start = data.get("starts_at")
        end = data.get("ends_at")
        if start is not None and end is not None and end < start:
            raise TimelineError("结束时间不能早于开始时间")
        data.setdefault("details", "")
        data.setdefault("kind", "todo")
        data.setdefault("status", "confirmed")
        data.setdefault("all_day", False)
        data.setdefault("timezone", "Asia/Shanghai")
        data.setdefault("location", "")
        data.setdefault("recurrence", "none")
        data.setdefault("notify", True)
        if partial:
            defaults = {"details", "kind", "status", "all_day", "timezone", "location", "recurrence", "notify"}
            for key in defaults - values.keys():
                data.pop(key, None)
        return data
