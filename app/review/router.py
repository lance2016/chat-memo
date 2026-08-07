"""每日回顾页的两个数据源：这一天的 digest，和跨天存活的悬而未决。

会话摘要、记忆版本、用量分别在 chat 和 memory 路由下，这里不重复。
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DailyDigest, OpenLoop
from app.db.session import get_session
from app.security import require_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["review"], dependencies=[Depends(require_api_key)])


class DigestOut(BaseModel):
    day: dt.date
    headline: str
    highlights: list[str]
    model: str
    created_at: Any
    updated_at: Any


class OpenLoopOut(BaseModel):
    id: int
    text: str
    opened_on: dt.date
    closed_on: dt.date | None
    closed_note: str | None
    status: str
    actor: str
    source_conversation_id: int | None


class OpenLoopCreate(BaseModel):
    text: str = Field(min_length=1)
    opened_on: dt.date | None = None


class CloseRequest(BaseModel):
    note: str = ""


def _to_out(loop: OpenLoop) -> OpenLoopOut:
    return OpenLoopOut(
        id=loop.id,
        text=loop.text,
        opened_on=loop.opened_on,
        closed_on=loop.closed_on,
        closed_note=loop.closed_note,
        status=loop.status,
        actor=loop.actor,
        source_conversation_id=loop.source_conversation_id,
    )


@router.get("/digests", response_model=DigestOut | None)
async def get_digest(
    day: dt.date,
    session: AsyncSession = Depends(get_session),
) -> DigestOut | None:
    """这一天的回顾。没整理过返回 null —— 那是常态，不是 404。"""
    digest = await session.scalar(select(DailyDigest).where(DailyDigest.day == day))
    if digest is None:
        return None
    return DigestOut(
        day=digest.day,
        headline=digest.headline,
        highlights=list(digest.highlights or []),
        model=digest.model,
        created_at=digest.created_at,
        updated_at=digest.updated_at,
    )


@router.get("/open-loops", response_model=list[OpenLoopOut])
async def list_open_loops(
    day: dt.date | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[OpenLoopOut]:
    """默认返回所有还挂着的。

    传 day 就是回顾页要的那一份：截至这天仍未闭环的 + 这天闭掉的（后者是页面上
    「今天闭环」那一段，划掉打勾的成就感来源）。注意「仍未闭环」要按日期算，
    否则翻看旧日期时会把之后才产生的待办也算进去。
    """
    stmt = select(OpenLoop).order_by(OpenLoop.opened_on, OpenLoop.id)
    if day is None:
        stmt = stmt.where(OpenLoop.status == "open")
    else:
        still_open = (OpenLoop.opened_on <= day) & (
            (OpenLoop.closed_on.is_(None)) | (OpenLoop.closed_on > day)
        )
        stmt = stmt.where(or_(still_open, OpenLoop.closed_on == day))
    return [_to_out(loop) for loop in (await session.execute(stmt)).scalars()]


@router.post("/open-loops", response_model=OpenLoopOut, status_code=status.HTTP_201_CREATED)
async def create_open_loop(
    payload: OpenLoopCreate,
    session: AsyncSession = Depends(get_session),
) -> OpenLoopOut:
    loop = OpenLoop(
        text=payload.text.strip(),
        opened_on=payload.opened_on or dt.date.today(),
        status="open",
        actor="manual",
    )
    session.add(loop)
    await session.commit()
    await session.refresh(loop)
    return _to_out(loop)


async def _get_loop(session: AsyncSession, loop_id: int) -> OpenLoop:
    loop = await session.get(OpenLoop, loop_id)
    if loop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "待办不存在")
    return loop


@router.post("/open-loops/{loop_id}/close", response_model=OpenLoopOut)
async def close_open_loop(
    loop_id: int,
    payload: CloseRequest,
    session: AsyncSession = Depends(get_session),
) -> OpenLoopOut:
    loop = await _get_loop(session, loop_id)
    loop.status = "closed"
    loop.closed_on = dt.date.today()
    loop.closed_note = payload.note.strip() or None
    loop.actor = "manual"
    await session.commit()
    await session.refresh(loop)
    return _to_out(loop)


@router.post("/open-loops/{loop_id}/reopen", response_model=OpenLoopOut)
async def reopen_open_loop(
    loop_id: int,
    session: AsyncSession = Depends(get_session),
) -> OpenLoopOut:
    """撤销闭环。模型误判闭环时的出口 —— 没有它，错标就是不可逆的。"""
    loop = await _get_loop(session, loop_id)
    loop.status = "open"
    loop.closed_on = None
    loop.closed_note = None
    loop.actor = "manual"
    await session.commit()
    await session.refresh(loop)
    return _to_out(loop)


@router.delete("/open-loops/{loop_id}", status_code=status.HTTP_204_NO_CONTENT)
async def drop_open_loop(
    loop_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """标记为「不做了」。不真删 —— 保留记录，也免得下次整理又抽出同一条。"""
    loop = await _get_loop(session, loop_id)
    loop.status = "dropped"
    loop.closed_on = dt.date.today()
    loop.actor = "manual"
    await session.commit()
