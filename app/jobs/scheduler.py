from __future__ import annotations

import asyncio
import datetime as dt
import logging

from app.config import get_settings
from app.db.session import get_sessionmaker
from app.jobs.consolidate import Consolidator
from app.llm.catalog import resolve_model_target
from app.llm.factory import get_provider
from app.notify.service import Notifier
from app.notify.sweep import sweep
from app.obs import trace
from app.settings_store import resolve_settings

logger = logging.getLogger(__name__)

# 提醒的时间精度。一分钟对「提前 15 分钟叫我」够用，也不值得更密 ——
# 每次 tick 都是一条带索引的查询，但空转一整天也是 1440 次。
TICK_SECONDS = 60


async def run_daily_consolidation() -> None:
    """每天在 settings.consolidate_hour 整理前一天的记忆。

    单人使用，asyncio 循环足够了 —— 不值得为此引入 Celery。代价是进程重启会错过当次，
    可以用 POST /api/jobs/consolidate?day=... 手动补。
    """
    settings = get_settings()
    while True:
        try:
            await asyncio.sleep(_seconds_until(settings.consolidate_hour))
        except asyncio.CancelledError:
            raise

        yesterday = dt.date.today() - dt.timedelta(days=1)
        try:
            with trace(
                "job",
                "daily-consolidation",
                purpose="consolidate",
                day=yesterday.isoformat(),
            ):
                async with get_sessionmaker()() as session:
                    settings = await resolve_settings(session)
                    target = await resolve_model_target(
                        session,
                        settings,
                        purpose="consolidation",
                        legacy_model_id=settings.consolidate_model,
                    )
                    result = await Consolidator(
                        session,
                        get_provider(settings, target=target),
                        settings,
                    ).run(yesterday)
                logger.info(
                    "记忆整理完成 date=%s 会话=%d 记忆写入=%d",
                    result.date,
                    result.summarized_conversations,
                    result.memory_writes,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # 整理失败不能拖垮进程，明天照常再试。
            logger.exception("记忆整理任务失败")


async def run_notification_ticker() -> None:
    """每分钟扫一次「该提醒什么」。

    **每轮都重新 resolve 一次设置**，不缓存启动时的快照：在设置页把通知打开、
    或者刚粘贴好 Bark key，应该下一分钟就生效，不必重启进程。这也是为什么循环本身
    不看 notify_enabled 就启动 —— 开关在循环内部判断。

    错过的提醒不会丢：sweep 查的是「该发而没发的」，不是「这一秒到点的」，
    所以笔记本睡醒之后会自动补上（补跑风暴的两道闸见 app/notify/sweep.py）。
    """
    while True:
        try:
            await asyncio.sleep(TICK_SECONDS)
        except asyncio.CancelledError:
            raise

        try:
            async with get_sessionmaker()() as session:
                settings = await resolve_settings(session)
                if not settings.notify_enabled:
                    continue
                notifier = Notifier(session, settings)
                if not notifier.ready:
                    continue
                count = await sweep(session, settings, notifier)
            if count:
                logger.info("主动通知已推送 %d 条", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 一次失败不能停掉整个循环，下一分钟照常再试。
            logger.exception("通知扫描失败")


def _seconds_until(hour: int) -> float:
    now = dt.datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()
