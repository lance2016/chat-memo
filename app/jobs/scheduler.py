from __future__ import annotations

import asyncio
import datetime as dt
import logging

from app import backup
from app.backup import run_backup
from app.db.session import get_sessionmaker
from app.jobs import backfill
from app.jobs.consolidate import Consolidator
from app.llm.catalog import resolve_model_target
from app.llm.factory import get_provider
from app.notify.service import Notifier
from app.notify.sweep import sweep
from app.obs import trace
from app.obs.context import set_current_span_attributes
from app.settings_store import resolve_settings

logger = logging.getLogger(__name__)

# 备份检查的间隔。比通知宽松得多 —— 备份晚十分钟没有任何代价，
# 而每分钟去 stat 一次目录纯属浪费。
BACKUP_TICK_SECONDS = 600

# 整理的检查间隔。补跑式判定很便宜（一次索引查询），但真跑一次要几十秒到几分钟，
# 所以查得比通知稀疏。晚十分钟开始整理昨天没有任何代价。
CONSOLIDATE_TICK_SECONDS = 600

# 提醒的时间精度。一分钟对「提前 15 分钟叫我」够用，也不值得更密 ——
# 每次 tick 都是一条带索引的查询，但空转一整天也是 1440 次。
TICK_SECONDS = 60


async def run_daily_consolidation() -> None:
    """定期检查「哪天该整理但没整理」，从旧到新补上。

    **不是定时到点跑**。原来是 `sleep 到 consolidate_hour` 然后整理昨天 ——
    进程一重启计时器就从头开始，笔记本凌晨多半在睡眠，于是这个开关默认只能关着，
    整理靠人记得手动触发。一个帮人记事的助手，自己的记忆整理却依赖人记得。

    改成补跑式之后（判据见 `app/jobs/backfill.py`），睡醒就补，默认可以开着。

    **一次 tick 只补一天**：一次完整的 agent loop 要跑几十秒到几分钟，
    串着补七天会把这个循环卡死很久，也会一口气烧掉一大笔 token。
    补完一天就返回，下一轮再补下一天 —— 反正没有人在等。
    """
    while True:
        try:
            await asyncio.sleep(CONSOLIDATE_TICK_SECONDS)
        except asyncio.CancelledError:
            raise

        try:
            async with get_sessionmaker()() as session:
                settings = await resolve_settings(session)
                if not settings.consolidate_auto:
                    continue
                days = await backfill.pending_days(session, settings)
                if not days:
                    continue
                day = days[0]

                with trace(
                    "job", "daily-consolidation",
                    purpose="consolidate", day=day.isoformat(),
                ):
                    target = await resolve_model_target(
                        session, settings,
                        purpose="consolidation",
                        legacy_model_id=settings.consolidate_model,
                    )
                    result = await Consolidator(
                        session, get_provider(settings, target=target), settings
                    ).run(day)
            logger.info(
                "记忆整理完成 date=%s 会话=%d 记忆写入=%d 待补 %d 天",
                result.date, result.summarized_conversations,
                result.memory_writes, len(days) - 1,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # 整理失败不能拖垮进程。失败的那天已经记进 consolidation_runs，
            # 不会被无限重试 —— 想重跑用 POST /api/jobs/consolidate?day=...
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
            count = 0
            with trace("job", "notify.tick", purpose="notify"):
                async with get_sessionmaker()() as session:
                    settings = await resolve_settings(session)
                    set_current_span_attributes(
                        **{
                            "notify.enabled": settings.notify_enabled,
                            "notify.channels_configured": settings.notify_channels,
                        }
                    )
                    if not settings.notify_enabled:
                        set_current_span_attributes(
                            **{"notify.skipped": True, "notify.reason": "disabled"}
                        )
                        continue
                    notifier = Notifier(session, settings)
                    if not notifier.ready:
                        set_current_span_attributes(
                            **{"notify.skipped": True, "notify.reason": "no_channel"}
                        )
                        continue
                    count = await sweep(session, settings, notifier)
                    set_current_span_attributes(**{"notify.sent_count": count})
            if count:
                logger.info("主动通知已推送 %d 条", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 一次失败不能停掉整个循环，下一分钟照常再试。
            logger.exception("通知扫描失败")


async def run_backup_ticker() -> None:
    """定期检查「今天备份过没有」，没有就补。

    **不是定时到点跑**：查的是有没有今天的 dump（`backup.is_due`），
    所以笔记本睡醒之后会自动补上。和 notify 的补跑式扫描同一套路子。

    间隔比通知宽松得多 —— 备份晚十分钟没有任何代价，而每分钟去 stat 一次目录
    纯属浪费。开关每轮重新读，在设置页关掉下一轮就生效。
    """
    while True:
        try:
            await asyncio.sleep(BACKUP_TICK_SECONDS)
        except asyncio.CancelledError:
            raise

        try:
            async with get_sessionmaker()() as session:
                settings = await resolve_settings(session)
                if not settings.backup_auto or not backup.is_due():
                    continue
                result = await run_backup(session, settings)
            if result.detail:
                # dump 失败不是致命错误（记忆文件已经导出了），但必须说出来 ——
                # 一个静默失败的备份等于没有备份。
                logger.error("自动备份未完成：%s", result.detail)
            else:
                logger.info(
                    "自动备份完成 %s · 轮换 %d 份", result.dump_file, len(result.pruned)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # 一次失败不能停掉整个循环，下一轮照常再试。
            logger.exception("自动备份失败")


def _seconds_until(hour: int) -> float:
    now = dt.datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()
