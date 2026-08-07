from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TimelineItem

KINDS = frozenset({"todo", "event", "reminder", "birthday", "travel", "deadline", "note"})
STATUSES = frozenset({"pending", "confirmed", "completed", "cancelled"})
RECURRENCES = frozenset({"none", "yearly"})


class TimelineError(ValueError):
    pass


class TimelineStore:
    def __init__(self, session: AsyncSession, *, actor: str, conversation_id: int | None = None) -> None:
        self.session = session
        self.actor = actor
        self.conversation_id = conversation_id

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
    ) -> list[TimelineItem]:
        stmt = select(TimelineItem).order_by(TimelineItem.starts_at, TimelineItem.id)
        if start is not None:
            stmt = stmt.where(TimelineItem.starts_at >= start)
        if end is not None:
            stmt = stmt.where(TimelineItem.starts_at < end)
        if statuses:
            unknown = statuses - STATUSES
            if unknown:
                raise TimelineError(f"未知状态：{', '.join(sorted(unknown))}")
            stmt = stmt.where(TimelineItem.status.in_(statuses))
        return list((await self.session.execute(stmt.limit(min(limit, 500)))).scalars())

    async def create(self, values: dict[str, Any]) -> TimelineItem:
        data = self._validated(values, partial=False)
        item = TimelineItem(
            **data,
            actor=self.actor,
            source_conversation_id=self.conversation_id,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def update(self, item_id: int, values: dict[str, Any]) -> TimelineItem:
        item = await self.get(item_id)
        for key, value in self._validated(values, partial=True).items():
            setattr(item, key, value)
        if item.ends_at is not None and item.ends_at < item.starts_at:
            raise TimelineError("结束时间不能早于开始时间")
        item.actor = self.actor
        await self.session.flush()
        return item

    async def delete(self, item_id: int) -> None:
        item = await self.get(item_id)
        await self.session.delete(item)
        await self.session.flush()

    def _validated(self, values: dict[str, Any], *, partial: bool) -> dict[str, Any]:
        allowed = {"title", "details", "kind", "status", "starts_at", "said", "ends_at", "all_day", "timezone", "location", "recurrence", "source_message_id"}
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
        if partial:
            defaults = {"details", "kind", "status", "all_day", "timezone", "location", "recurrence"}
            for key in defaults - values.keys():
                data.pop(key, None)
        return data
