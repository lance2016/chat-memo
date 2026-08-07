from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TimelineItem
from app.db.session import get_session
from app.security import require_api_key
from app.settings_store import resolve_settings
from app.timeline.store import TimelineError, TimelineStore

router = APIRouter(prefix="/api/timeline", tags=["timeline"], dependencies=[Depends(require_api_key)])

Kind = Literal["todo", "event", "reminder", "birthday", "travel", "deadline", "note"]
ItemStatus = Literal["pending", "confirmed", "completed", "cancelled"]
Recurrence = Literal["none", "yearly"]


class TimelineCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    details: str = ""
    kind: Kind = "todo"
    status: ItemStatus = "confirmed"
    starts_at: dt.datetime
    ends_at: dt.datetime | None = None
    all_day: bool = False
    timezone: str = "Asia/Shanghai"
    location: str = ""
    recurrence: Recurrence = "none"
    notify: bool = True
    lead_minutes: int | None = Field(default=None, ge=0, le=30 * 24 * 60)


class TimelineUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    details: str | None = None
    kind: Kind | None = None
    status: ItemStatus | None = None
    starts_at: dt.datetime | None = None
    ends_at: dt.datetime | None = None
    all_day: bool | None = None
    timezone: str | None = None
    location: str | None = None
    recurrence: Recurrence | None = None
    notify: bool | None = None
    lead_minutes: int | None = Field(default=None, ge=0, le=30 * 24 * 60)


class SnoozeRequest(BaseModel):
    minutes: int = Field(default=30, ge=1, le=7 * 24 * 60)


class TimelineOut(BaseModel):
    id: int
    title: str
    details: str
    kind: str
    status: str
    starts_at: dt.datetime
    # 用户原话里的时间依据。时间看着不对时，用它分辨是他说得含糊还是模型解析错了。
    said: str
    ends_at: dt.datetime | None
    all_day: bool
    timezone: str
    location: str
    recurrence: str
    actor: str
    source_conversation_id: int | None
    source_message_id: int | None
    notify: bool
    lead_minutes: int | None
    # 实际会在什么时刻推送。null = 这条不提醒。前端直接显示它，
    # 免得用户要自己拿 starts_at 减提前量。
    remind_at: dt.datetime | None
    snoozed_until: dt.datetime | None
    created_at: Any
    updated_at: Any


def _out(item: TimelineItem) -> TimelineOut:
    return TimelineOut(**{field: getattr(item, field) for field in TimelineOut.model_fields})


async def _store(session: AsyncSession) -> TimelineStore:
    # 走合并配置：提醒时刻依赖 notify_default_lead_minutes / notify_all_day_hour，
    # 而这两项在设置页可改。用 .env 快照会让「改了提前量却不生效」。
    return TimelineStore(session, actor="manual", settings=await resolve_settings(session))


def _bad_request(exc: TimelineError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("", response_model=list[TimelineOut])
async def list_timeline(
    start: dt.datetime | None = Query(default=None, alias="from"),
    end: dt.datetime | None = Query(default=None, alias="to"),
    status_filter: str | None = Query(default=None, alias="status"),
    include_overdue: bool = Query(default=False, alias="include_overdue"),
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
) -> list[TimelineOut]:
    statuses = set(status_filter.split(",")) if status_filter else None
    try:
        items = await (await _store(session)).list(
            start=start,
            end=end,
            statuses=statuses,
            limit=limit,
            include_overdue=include_overdue,
        )
    except TimelineError as exc:
        raise _bad_request(exc) from exc
    return [_out(item) for item in items]


@router.post("", response_model=TimelineOut, status_code=status.HTTP_201_CREATED)
async def create_timeline_item(payload: TimelineCreate, session: AsyncSession = Depends(get_session)) -> TimelineOut:
    try:
        item = await (await _store(session)).create(payload.model_dump())
    except TimelineError as exc:
        raise _bad_request(exc) from exc
    await session.commit()
    await session.refresh(item)
    return _out(item)


@router.post("/{item_id}/snooze", response_model=TimelineOut)
async def snooze_timeline_item(
    item_id: int,
    payload: SnoozeRequest,
    session: AsyncSession = Depends(get_session),
) -> TimelineOut:
    store = await _store(session)
    try:
        item = await store.snooze(item_id, payload.minutes)
    except TimelineError as exc:
        await session.rollback()
        if str(exc) == "时间事项不存在":
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        raise _bad_request(exc) from exc
    await session.commit()
    await session.refresh(item)
    return _out(item)


@router.patch("/{item_id}", response_model=TimelineOut)
async def update_timeline_item(item_id: int, payload: TimelineUpdate, session: AsyncSession = Depends(get_session)) -> TimelineOut:
    store = await _store(session)
    try:
        item = await store.update(item_id, payload.model_dump(exclude_unset=True))
        if item.ends_at is not None and item.ends_at < item.starts_at:
            raise TimelineError("结束时间不能早于开始时间")
    except TimelineError as exc:
        await session.rollback()
        if str(exc) == "时间事项不存在":
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        raise _bad_request(exc) from exc
    await session.commit()
    await session.refresh(item)
    return _out(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_timeline_item(item_id: int, session: AsyncSession = Depends(get_session)) -> None:
    try:
        await (await _store(session)).delete(item_id)
    except TimelineError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await session.commit()
