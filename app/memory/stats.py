"""记忆使用率统计。

回答一个问题：**攒的这些记忆到底有没有用上。**

读取来自 ``memory_reads`` 埋点，写入来自已有的 ``memory_versions``，两边不重复记账。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Memory, MemoryRead, MemoryVersion
from app.memory.paths import INDEX_PATH
from app.timeutils import local_day_bounds


@dataclass
class MemoryUsage:
    path: str
    reads: int
    writes: int
    last_read_at: dt.datetime | None
    created_at: dt.datetime
    idle_days: int | None  # 距上次被读的天数；从未被读则为 None
    # 判断噪音必须结合长度看：索引里每条都有一行摘要，短事实（「住在杭州」）
    # 靠摘要就答完了，压根不需要 view —— reads=0 是正常的。
    # 真正可疑的是「内容很长但从没被打开过」：写了一堆细节却从没用上。
    content_chars: int


@dataclass
class DailyActivity:
    day: str
    reads: int
    writes: int


@dataclass
class ActorBreakdown:
    actor: str
    reads: int
    writes: int


@dataclass
class MemoryStats:
    total_memories: int
    total_reads: int
    total_writes: int
    never_read: int
    # 模型想读但不存在的路径数。偏高说明索引和实际内容对不上。
    missed_reads: int
    daily: list[DailyActivity] = field(default_factory=list)
    top: list[MemoryUsage] = field(default_factory=list)
    unused: list[MemoryUsage] = field(default_factory=list)
    by_actor: list[ActorBreakdown] = field(default_factory=list)


async def collect_stats(
    session: AsyncSession, days: int = 30, top_n: int = 10
) -> MemoryStats:
    memories = list((await session.execute(select(Memory))).scalars())

    read_counts = dict(
        (
            await session.execute(
                select(MemoryRead.path, func.count(MemoryRead.id))
                .where(MemoryRead.found.is_(True))
                .group_by(MemoryRead.path)
            )
        ).all()
    )
    last_reads = dict(
        (
            await session.execute(
                select(MemoryRead.path, func.max(MemoryRead.created_at))
                .where(MemoryRead.found.is_(True))
                .group_by(MemoryRead.path)
            )
        ).all()
    )
    write_counts = dict(
        (
            await session.execute(
                select(MemoryVersion.path, func.count(MemoryVersion.id)).group_by(
                    MemoryVersion.path
                )
            )
        ).all()
    )

    now = dt.datetime.now(dt.UTC)
    usage = [
        MemoryUsage(
            path=m.path,
            reads=read_counts.get(m.path, 0),
            writes=write_counts.get(m.path, 0),
            last_read_at=last_reads.get(m.path),
            created_at=m.created_at,
            idle_days=_days_since(last_reads.get(m.path), now),
            content_chars=len(m.content),
        )
        for m in memories
    ]

    # 索引每轮都会自动注入 system prompt，不该和「模型主动去读」的条目一起排名。
    ranked = sorted(
        (u for u in usage if u.path != INDEX_PATH),
        key=lambda u: (u.reads, u.writes),
        reverse=True,
    )
    # 从没被打开过的按内容长度倒序 —— 越长越可疑，短的多半是索引摘要就够用了。
    unused = sorted(
        (u for u in ranked if u.reads == 0), key=lambda u: u.content_chars, reverse=True
    )

    missed = await session.scalar(
        select(func.count(MemoryRead.id)).where(MemoryRead.found.is_(False))
    )

    return MemoryStats(
        total_memories=len(memories),
        total_reads=sum(read_counts.values()),
        total_writes=sum(write_counts.values()),
        never_read=len(unused),
        missed_reads=missed or 0,
        daily=await _daily(session, days),
        top=[u for u in ranked if u.reads][:top_n],
        unused=unused[:top_n],
        by_actor=await _by_actor(session),
    )


def _days_since(when: dt.datetime | None, now: dt.datetime) -> int | None:
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return max((now - when).days, 0)


async def _daily(session: AsyncSession, days: int) -> list[DailyActivity]:
    """按天统计。逐日查而不是 SQL 里 date_trunc —— 那样才能按本地时区切天。"""
    today = dt.date.today()
    out: list[DailyActivity] = []
    for offset in range(min(days, 180) - 1, -1, -1):
        day = today - dt.timedelta(days=offset)
        start, end = local_day_bounds(day)
        reads = await session.scalar(
            select(func.count(MemoryRead.id)).where(
                MemoryRead.created_at >= start, MemoryRead.created_at < end
            )
        )
        writes = await session.scalar(
            select(func.count(MemoryVersion.id)).where(
                MemoryVersion.created_at >= start, MemoryVersion.created_at < end
            )
        )
        out.append(DailyActivity(day=day.isoformat(), reads=reads or 0, writes=writes or 0))
    return out


async def _by_actor(session: AsyncSession) -> list[ActorBreakdown]:
    reads = dict(
        (
            await session.execute(
                select(MemoryRead.actor, func.count(MemoryRead.id)).group_by(
                    MemoryRead.actor
                )
            )
        ).all()
    )
    writes = dict(
        (
            await session.execute(
                select(MemoryVersion.actor, func.count(MemoryVersion.id)).group_by(
                    MemoryVersion.actor
                )
            )
        ).all()
    )
    return [
        ActorBreakdown(actor=actor, reads=reads.get(actor, 0), writes=writes.get(actor, 0))
        for actor in sorted(set(reads) | set(writes))
    ]
