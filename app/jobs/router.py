from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings_store import resolve_settings
from app.backup import run_backup
from app.db.session import get_session
from app.jobs.consolidate import Consolidator
from app.llm.factory import get_provider
from app.security import require_api_key

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


@router.post("/consolidate", response_model=ConsolidateOut)
async def consolidate(
    day: dt.date | None = None,
    session: AsyncSession = Depends(get_session),
) -> ConsolidateOut:
    """手动触发记忆整理。不传 day 就整理今天。"""
    settings = await resolve_settings(session)
    provider = get_provider(settings, model_override=settings.consolidate_model)
    result = await Consolidator(session, provider).run(day)
    return ConsolidateOut(**result.__dict__)


class BackupOut(BaseModel):
    dump_file: str
    dump_bytes: int
    memory_files: int
    memory_dir: str
    created_at: str
    detail: str


@router.post("/backup", response_model=BackupOut)
async def backup(session: AsyncSession = Depends(get_session)) -> BackupOut:
    """全量快照 + 把记忆导出成可读的 .md 文件树。

    ``detail`` 非空表示 dump 那部分出了问题（例如镜像里没装 pg_dump），
    此时记忆文件仍然导出成功了。
    """
    settings = await resolve_settings(session)
    return BackupOut(**(await run_backup(session, settings)).__dict__)
