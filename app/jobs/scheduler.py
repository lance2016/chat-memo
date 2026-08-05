from __future__ import annotations

import asyncio
import datetime as dt
import logging

from app.config import get_settings
from app.db.session import get_sessionmaker
from app.jobs.consolidate import Consolidator
from app.llm.factory import get_provider

logger = logging.getLogger(__name__)


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
            async with get_sessionmaker()() as session:
                result = await Consolidator(session, get_provider(model_override=get_settings().consolidate_model)).run(yesterday)
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


def _seconds_until(hour: int) -> float:
    now = dt.datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()
