from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
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


@router.post("/consolidate", response_model=ConsolidateOut)
async def consolidate(
    day: dt.date | None = None,
    session: AsyncSession = Depends(get_session),
) -> ConsolidateOut:
    """手动触发记忆整理。不传 day 就整理今天。"""
    result = await Consolidator(session, get_provider(model_override=get_settings().consolidate_model)).run(day)
    return ConsolidateOut(**result.__dict__)
