from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.backup import run_backup
from app.db.session import get_session
from app.jobs import backfill
from app.jobs.consolidate import Consolidator
from app.llm.catalog import resolve_model_target
from app.llm.factory import get_provider
from app.security import require_api_key
from app.settings_store import resolve_settings

router = APIRouter(
    prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_api_key)]
)


class ConsolidateOut(BaseModel):
    date: str
    summarized_conversations: int
    tool_calls: int
    memory_writes: int
    skipped: bool
    failed_summaries: int
    detail: str
    headline: str
    title: str
    new_loops: int
    closed_loops: int
    digest_failed: bool
    index_issues: int
    index_report: str


class ConsolidationRunOut(BaseModel):
    day: str
    status: str
    detail: str
    summarized_conversations: int
    memory_writes: int
    index_issues: int
    seconds: float


class ConsolidationHealthOut(BaseModel):
    """整理这条核心循环健不健康。

    **「静默不运转」比「报错」危险得多** —— 整理不跑的时候没有任何症状，
    要过好几天才隐约觉得「它怎么不记得了」。这个接口就是那双眼睛。
    """

    auto: bool
    # 该整理但还没整理的日子。非空说明正在补，或者补不动了
    pending: list[str]
    # 最近一次整理是哪天、什么结果
    last_day: str
    last_status: str
    runs: list[ConsolidationRunOut]


@router.get("/consolidate/health", response_model=ConsolidationHealthOut)
async def consolidation_health(
    session: AsyncSession = Depends(get_session),
) -> ConsolidationHealthOut:
    settings = await resolve_settings(session)
    runs = await backfill.recent_runs(session)
    pending = await backfill.pending_days(session, settings)
    return ConsolidationHealthOut(
        auto=settings.consolidate_auto,
        pending=[day.isoformat() for day in pending],
        last_day=runs[0].day.isoformat() if runs else "",
        last_status=runs[0].status if runs else "",
        runs=[
            ConsolidationRunOut(
                day=run.day.isoformat(),
                status=run.status,
                detail=run.detail,
                summarized_conversations=run.summarized_conversations,
                memory_writes=run.memory_writes,
                index_issues=run.index_issues,
                seconds=run.seconds,
            )
            for run in runs
        ],
    )


@router.post("/consolidate", response_model=ConsolidateOut)
async def consolidate(
    day: dt.date | None = None,
    session: AsyncSession = Depends(get_session),
) -> ConsolidateOut:
    """手动触发记忆整理。不传 day 就整理今天。"""
    settings = await resolve_settings(session)
    target = await resolve_model_target(
        session,
        settings,
        purpose="consolidation",
        legacy_model_id=settings.consolidate_model,
    )
    provider = get_provider(settings, target=target)
    result = await Consolidator(session, provider, settings).run(day)
    return ConsolidateOut(**result.__dict__)


class BackupOut(BaseModel):
    dump_file: str
    dump_bytes: int
    memory_files: int
    memory_dir: str
    # 附件正文是磁盘上的，不在 dump 里。回显出来才看得见它确实被备份了。
    attachment_files: int = 0
    attachment_bytes: int = 0
    created_at: str
    detail: str
    # 本次轮换掉的旧备份。不回显的话「留最近 N 份」这个设置是完全不可见的
    pruned: list[str] = []


@router.post("/backup", response_model=BackupOut)
async def backup(session: AsyncSession = Depends(get_session)) -> BackupOut:
    """全量快照 + 把记忆导出成可读的 .md 文件树。

    ``detail`` 非空表示 dump 那部分出了问题（例如镜像里没装 pg_dump），
    此时记忆文件仍然导出成功了。
    """
    settings = await resolve_settings(session)
    return BackupOut(**(await run_backup(session, settings)).__dict__)
