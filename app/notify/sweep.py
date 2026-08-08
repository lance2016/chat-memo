"""扫一遍「现在该提醒什么」。

**补跑式，不是定时触发式**：每次 tick 查的是「该发而没发的」，不是「这一秒到点的」。
`app/jobs/scheduler.py` 里已经写过这个教训 —— 进程一重启计时器就从头开始，笔记本
凌晨多半在睡眠。定时触发在这台机器上必然漏，查询式则是睡醒就补上。

代价是要挡住「补跑风暴」：离线一周后醒来，不能把一周的提醒一次性全推到手机上。
两道闸：太老的事项不再单独响（交给每日简报兜底），单次 tick 有条数上限。
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import TimelineItem
from app.notify.compose import compose_body, format_clock, kind_emoji, subtitle_for
from app.notify.message import PushMessage
from app.notify.service import Notifier
from app.obs import trace
from app.timeutils import aware, local_day_bounds

logger = logging.getLogger(__name__)

# 活着的事项 —— 做完和取消的都不该再响。
LIVE_STATUSES = ("pending", "confirmed")

# 单次 tick 最多推几条。超出的留到下一分钟，避免离线很久之后一次性刷屏。
MAX_PER_TICK = 5

# 简报的有效窗口（小时）。错过就跳过这一天，而不是晚上十点推一条「今天有三件事」。
BRIEFING_WINDOW_HOURS = 6

# 待确认事项挂多久算「该催了」。
STALE_PENDING_DAYS = 14


def _link(settings: Settings, item: TimelineItem | None = None) -> str:
    """通知点开后去哪。没配公网地址就不带链接 —— 手机点开一个 localhost 只会失败。"""
    base = settings.notify_public_base_url.strip().rstrip("/")
    if not base:
        return ""
    if item is not None and item.source_conversation_id is not None:
        return f"{base}/?conversation={item.source_conversation_id}"
    return f"{base}/timeline"


async def due_items(
    session: AsyncSession, now: dt.datetime, *, catchup_hours: int, limit: int
) -> list[TimelineItem]:
    """到点该单独提醒的事项。"""
    stmt = (
        select(TimelineItem)
        .where(
            TimelineItem.notify.is_(True),
            TimelineItem.status.in_(LIVE_STATUSES),
            TimelineItem.remind_at.is_not(None),
            TimelineItem.remind_at <= now,
            or_(
                TimelineItem.snoozed_until.is_(None),
                TimelineItem.snoozed_until <= now,
            ),
            # 太老的不再单独响。这类漏掉的由简报的「逾期」段落一次性交代。
            TimelineItem.starts_at >= now - dt.timedelta(hours=catchup_hours),
        )
        .order_by(TimelineItem.starts_at, TimelineItem.id)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


def dedupe_key_for(item: TimelineItem) -> str:
    """改期和 snooze 都会换 key —— 换了就是一条新提醒，本来就该再响一次。"""
    moment = item.snoozed_until or item.remind_at
    stamp = aware(moment).astimezone(dt.UTC).strftime("%Y%m%dT%H%M") if moment else "none"
    return f"item:{item.id}:{stamp}"


async def sweep_due(
    session: AsyncSession, settings: Settings, notifier: Notifier, now: dt.datetime
) -> int:
    items = await due_items(
        session,
        now,
        catchup_hours=settings.notify_catchup_hours,
        limit=MAX_PER_TICK,
    )
    sent = 0
    for item in items:
        body = await compose_body(session, item, settings, now)
        message = PushMessage(
            dedupe_key=dedupe_key_for(item),
            kind="item",
            title=f"{kind_emoji(item.kind)} {item.title}",
            subtitle=subtitle_for(item),
            body=body,
            url=_link(settings, item),
            group="时间线",
            # 一小时内就要开始的事才配打断专注模式。
            level="timeSensitive"
            if aware(item.starts_at) - aware(now) <= dt.timedelta(hours=1)
            else "active",
            timeline_item_id=item.id,
        )
        if await notifier.deliver(message) is not None:
            sent += 1
    return sent


async def briefing_sections(
    session: AsyncSession, now: dt.datetime
) -> tuple[list[TimelineItem], list[TimelineItem], list[TimelineItem]]:
    """简报的三段：今天、逾期未完成、挂太久的待确认。"""
    start, end = local_day_bounds(now.astimezone().date())

    today = list(
        (
            await session.execute(
                select(TimelineItem)
                .where(
                    TimelineItem.status.in_(LIVE_STATUSES),
                    TimelineItem.starts_at >= start,
                    TimelineItem.starts_at < end,
                )
                .order_by(TimelineItem.all_day.desc(), TimelineItem.starts_at)
                .limit(20)
            )
        ).scalars()
    )
    overdue = list(
        (
            await session.execute(
                select(TimelineItem)
                .where(
                    TimelineItem.status.in_(LIVE_STATUSES),
                    TimelineItem.starts_at < start,
                )
                .order_by(TimelineItem.starts_at.desc())
                .limit(10)
            )
        ).scalars()
    )
    stale = list(
        (
            await session.execute(
                select(TimelineItem)
                .where(
                    TimelineItem.status == "pending",
                    TimelineItem.created_at
                    < now - dt.timedelta(days=STALE_PENDING_DAYS),
                    TimelineItem.starts_at >= start,
                )
                .order_by(TimelineItem.created_at)
                .limit(5)
            )
        ).scalars()
    )
    return today, overdue, stale


def briefing_text(
    today: list[TimelineItem], overdue: list[TimelineItem], stale: list[TimelineItem]
) -> tuple[str, str]:
    """(标题, 正文)。标题一眼看清今天几件事，正文逐条列。"""
    title = f"☀️ 今天有 {len(today)} 件事" if today else "☀️ 今天没有安排"
    lines = [
        f"{kind_emoji(item.kind)} {format_clock(item)} {item.title}" for item in today
    ]
    if overdue:
        lines.append("")
        lines.append(f"逾期未完成 {len(overdue)} 件：")
        lines += [
            f"· {item.starts_at.astimezone():%-m/%-d} {item.title}" for item in overdue[:5]
        ]
    if stale:
        lines.append("")
        lines.append(f"还有 {len(stale)} 件待确认挂了两周以上")
    return title, "\n".join(lines)


def briefing_due(settings: Settings, now: dt.datetime) -> bool:
    """在简报窗口内。错过整个窗口就跳过这一天。"""
    hour = now.astimezone().hour
    return (
        settings.notify_briefing
        and settings.notify_briefing_hour
        <= hour
        < settings.notify_briefing_hour + BRIEFING_WINDOW_HOURS
    )


async def sweep_briefing(
    session: AsyncSession, settings: Settings, notifier: Notifier, now: dt.datetime
) -> int:
    if not briefing_due(settings, now):
        return 0

    today, overdue, stale = await briefing_sections(session, now)
    if not (today or overdue or stale):
        # 什么都没有就不推。「今天没有安排」每天来一条纯属噪音。
        return 0

    title, body = briefing_text(today, overdue, stale)
    message = PushMessage(
        dedupe_key=f"briefing:{now.astimezone():%Y-%m-%d}",
        kind="briefing",
        title=title,
        subtitle=f"{now.astimezone():%-m月%-d日}",
        body=body,
        url=_link(settings),
        group="每日简报",
        level="active",
    )
    return 1 if await notifier.deliver(message) is not None else 0


async def sweep(
    session: AsyncSession,
    settings: Settings,
    notifier: Notifier,
    now: dt.datetime | None = None,
) -> int:
    """跑一轮，返回实际推出去的条数。"""
    with trace("job", "notify.sweep", purpose="notify"):
        return await _sweep(session, settings, notifier, now)


async def _sweep(
    session: AsyncSession,
    settings: Settings,
    notifier: Notifier,
    now: dt.datetime | None = None,
) -> int:
    now = now or dt.datetime.now(dt.UTC)
    return await sweep_briefing(session, settings, notifier, now) + await sweep_due(
        session, settings, notifier, now
    )
