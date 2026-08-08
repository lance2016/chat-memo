"""每日整理的补跑判定。

**「一个帮人记事的助手，自己的记忆整理却依赖人记得去触发」** —— roadmap 里这句话
是这个模块存在的理由。原来 `consolidate_auto` 默认关着，注释写得很明白：进程一重启
计时器就从头开始，笔记本凌晨多半在睡眠，定时器很容易整天不触发。

解法在这个仓库里已经验证过两遍（notify 的提醒扫描、备份的 ticker）：
**查「该做而没做的」，不做精确定时，睡醒就补。**

和备份的区别在于这里必须建表。备份用文件名当记录就够了，而整理可能合法地
「什么都没做」（那天没有值得沉淀的内容）—— 那种情况下没有任何产物，
不显式记一笔的话每次 tick 都会重跑同一天，白烧 token。
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ConsolidationRun, Message, live_message
from app.timeutils import local_day_bounds

logger = logging.getLogger(__name__)

# 最多往回补几天。离线一周后醒来，不该把一周的整理一次性全跑掉 ——
# 那是七次完整的 agent loop，token 和时间都不划算，而且太老的日子补出来的
# 记忆价值也在衰减。超出窗口的日子直接放弃，宁可漏也不要雪崩。
MAX_BACKFILL_DAYS = 7


async def pending_days(
    session: AsyncSession,
    settings: Settings,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
) -> list[dt.date]:
    """该整理但还没整理的日子，**从旧到新**。

    三道过滤：

    1. 只看已经结束的日子。今天还在往里写对话，整理了也不完整
    2. 昨天要等过了 `consolidate_hour` 才算数 —— 保留原来「凌晨四点再动」的意图，
       避免刚过午夜就开始烧 token
    3. 已经有记录的跳过，**不管那次是成功、跳过还是失败**。失败的日子留给人手动重跑：
       自动无限重试一个总是失败的日子，只会每十分钟烧一次 token
    """
    today = today or dt.date.today()
    now = now or dt.datetime.now()

    latest = today - dt.timedelta(days=1)
    if now.hour < settings.consolidate_hour:
        # 还没到点，昨天先不动
        latest = today - dt.timedelta(days=2)

    earliest = today - dt.timedelta(days=MAX_BACKFILL_DAYS)
    if latest < earliest:
        return []

    done = set(
        (
            await session.execute(
                select(ConsolidationRun.day).where(
                    ConsolidationRun.day >= earliest, ConsolidationRun.day <= latest
                )
            )
        )
        .scalars()
        .all()
    )

    candidates = [
        day
        for offset in range((latest - earliest).days + 1)
        if (day := earliest + dt.timedelta(days=offset)) not in done
    ]
    if not candidates:
        return []

    # 没有任何对话的日子不值得跑一遍 agent loop，但**要记一笔**，
    # 否则每次 tick 都会重新发现它。记录由调用方在跑完后写。
    return [day for day in candidates if await _has_messages(session, day)]


async def _has_messages(session: AsyncSession, day: dt.date) -> bool:
    start, end = local_day_bounds(day)
    # 整条都被撤下的日子不算「有内容」，否则会白跑一次 agent loop。
    found = await session.scalar(
        select(Message.id)
        .where(Message.created_at >= start, Message.created_at < end, live_message())
        .limit(1)
    )
    return found is not None


async def record(
    session: AsyncSession,
    day: dt.date,
    *,
    status: str,
    detail: str = "",
    summarized_conversations: int = 0,
    memory_writes: int = 0,
    index_issues: int = 0,
    seconds: float = 0.0,
) -> ConsolidationRun:
    """记一笔。一天一行，重跑是覆盖 —— 这是「这天整理成什么样」的当前答案，不是流水。"""
    run = await session.scalar(
        select(ConsolidationRun).where(ConsolidationRun.day == day)
    )
    if run is None:
        run = ConsolidationRun(day=day)
        session.add(run)
    run.status = status
    run.detail = detail[:2000]
    run.summarized_conversations = summarized_conversations
    run.memory_writes = memory_writes
    run.index_issues = index_issues
    run.seconds = seconds
    return run


async def recent_runs(
    session: AsyncSession, limit: int = 14
) -> list[ConsolidationRun]:
    rows = await session.execute(
        select(ConsolidationRun).order_by(ConsolidationRun.day.desc()).limit(limit)
    )
    return list(rows.scalars())
