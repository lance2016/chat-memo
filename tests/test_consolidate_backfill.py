"""每日整理的补跑判定。

roadmap P0-1 的验收标准：跨过 `consolidate_hour`、杀进程重启，观察补跑且
`consolidation_runs` 有记录。这里用注入时钟代替改系统时间。

这块要挡住的是**静默不运转**：整理是核心循环，它不跑的时候没有任何症状，
要过好几天才隐约觉得「它怎么不记得了」。
"""

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Conversation, Message
from app.jobs import backfill

TODAY = dt.date(2026, 8, 8)


def _settings(**overrides) -> Settings:
    return Settings(consolidate_hour=4, **overrides)


async def seed_day(session: AsyncSession, day: dt.date) -> None:
    when = dt.datetime.combine(day, dt.time(12, 0)).astimezone()
    conversation = Conversation(title=f"{day} 的会话", created_at=when, updated_at=when)
    session.add(conversation)
    await session.flush()
    session.add(
        Message(
            conversation_id=conversation.id,
            role="user",
            content=[{"type": "text", "text": "聊点什么"}],
            created_at=when,
        )
    )
    await session.commit()


async def test_yesterday_is_pending_after_the_hour(session: AsyncSession) -> None:
    await seed_day(session, TODAY - dt.timedelta(days=1))

    days = await backfill.pending_days(
        session, _settings(), TODAY, dt.datetime(2026, 8, 8, 5, 0)
    )

    assert days == [TODAY - dt.timedelta(days=1)]


async def test_yesterday_waits_until_the_hour(session: AsyncSession) -> None:
    """保留「凌晨四点再动」的意图 —— 刚过午夜就开始烧 token 没必要。"""
    await seed_day(session, TODAY - dt.timedelta(days=1))

    days = await backfill.pending_days(
        session, _settings(), TODAY, dt.datetime(2026, 8, 8, 1, 0)
    )

    assert days == []


async def test_today_is_never_pending(session: AsyncSession) -> None:
    """今天还在往里写对话，整理了也不完整。"""
    await seed_day(session, TODAY)

    days = await backfill.pending_days(
        session, _settings(), TODAY, dt.datetime(2026, 8, 8, 23, 0)
    )

    assert TODAY not in days


async def test_missed_days_are_caught_up_oldest_first(session: AsyncSession) -> None:
    """笔记本睡了三天醒来 —— 这正是原来那个定时器扛不住的场景。

    从旧到新：记忆整理有累积语义，先整理早的那天才是对的顺序。
    """
    for offset in (1, 2, 3):
        await seed_day(session, TODAY - dt.timedelta(days=offset))

    days = await backfill.pending_days(
        session, _settings(), TODAY, dt.datetime(2026, 8, 8, 5, 0)
    )

    assert days == [
        TODAY - dt.timedelta(days=3),
        TODAY - dt.timedelta(days=2),
        TODAY - dt.timedelta(days=1),
    ]


async def test_recorded_days_are_not_redone(session: AsyncSession) -> None:
    day = TODAY - dt.timedelta(days=1)
    await seed_day(session, day)
    await backfill.record(session, day, status="ok")
    await session.commit()

    days = await backfill.pending_days(
        session, _settings(), TODAY, dt.datetime(2026, 8, 8, 5, 0)
    )

    assert days == []


async def test_a_skipped_day_counts_as_done(session: AsyncSession) -> None:
    """整理可能合法地「什么都没做」（那天没有值得沉淀的内容）。

    不记这一笔的话每次 tick 都会重跑同一天 —— 这正是备份那边能用文件名当记录、
    这边却必须建表的原因。
    """
    day = TODAY - dt.timedelta(days=1)
    await seed_day(session, day)
    await backfill.record(session, day, status="skipped")
    await session.commit()

    assert await backfill.pending_days(
        session, _settings(), TODAY, dt.datetime(2026, 8, 8, 5, 0)
    ) == []


async def test_a_failed_day_is_not_retried_forever(session: AsyncSession) -> None:
    """失败的日子留给人手动重跑。

    自动无限重试一个总是失败的日子，只会每十分钟烧一次 token —— 而且大概率
    每次都以同样的方式失败。
    """
    day = TODAY - dt.timedelta(days=1)
    await seed_day(session, day)
    await backfill.record(session, day, status="failed", detail="provider 挂了")
    await session.commit()

    assert await backfill.pending_days(
        session, _settings(), TODAY, dt.datetime(2026, 8, 8, 5, 0)
    ) == []


async def test_days_without_conversations_are_skipped(session: AsyncSession) -> None:
    """没有任何对话的日子不值得跑一遍 agent loop。"""
    days = await backfill.pending_days(
        session, _settings(), TODAY, dt.datetime(2026, 8, 8, 5, 0)
    )

    assert days == []


async def test_backfill_window_is_bounded(session: AsyncSession) -> None:
    """离线很久后醒来，不该把一个月的整理一次性全跑掉。

    那是几十次完整的 agent loop，而且太老的日子补出来的记忆价值也在衰减。
    宁可漏也不要雪崩 —— 和 notify 的补跑风暴闸门同一个道理。
    """
    old = TODAY - dt.timedelta(days=backfill.MAX_BACKFILL_DAYS + 3)
    await seed_day(session, old)
    await seed_day(session, TODAY - dt.timedelta(days=1))

    days = await backfill.pending_days(
        session, _settings(), TODAY, dt.datetime(2026, 8, 8, 5, 0)
    )

    assert old not in days
    assert days == [TODAY - dt.timedelta(days=1)]


async def test_record_is_one_row_per_day(session: AsyncSession) -> None:
    """重跑是覆盖 —— 这是「这天整理成什么样」的当前答案，不是流水。"""
    day = TODAY - dt.timedelta(days=1)
    await backfill.record(session, day, status="failed", detail="第一次挂了")
    await session.commit()
    await backfill.record(session, day, status="ok", memory_writes=3)
    await session.commit()

    runs = await backfill.recent_runs(session)

    assert len(runs) == 1
    assert runs[0].status == "ok" and runs[0].memory_writes == 3
