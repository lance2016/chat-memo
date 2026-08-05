from __future__ import annotations

import datetime as dt
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MemoryVersion
from app.db.session import get_session
from app.memory.errors import InvalidMemoryPath, MemoryNotFound, MemoryToolError
from app.memory.paths import MEMORY_ROOT
from app.memory.stats import collect_stats
from app.memory.store import MemoryStore
from app.security import require_api_key
from app.timeutils import local_day_bounds

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/memories", tags=["memories"], dependencies=[Depends(require_api_key)]
)


class MemoryNodeOut(BaseModel):
    path: str
    is_dir: bool
    size: int


class MemoryOut(BaseModel):
    path: str
    content: str
    created_at: Any
    updated_at: Any


class MemoryVersionOut(BaseModel):
    id: int
    path: str
    content: str
    operation: str
    actor: str
    created_at: Any


class MemoryWrite(BaseModel):
    content: str = Field(default="")


class RestoreRequest(BaseModel):
    version_id: int


class MemoryUsageOut(BaseModel):
    path: str
    reads: int
    writes: int
    last_read_at: Any
    created_at: Any
    # 距上次被读的天数；null 表示从未被读过
    idle_days: int | None
    # 判断噪音要结合长度：短记忆靠索引摘要就够用，reads=0 属正常
    content_chars: int


class DailyActivityOut(BaseModel):
    day: str
    reads: int
    writes: int


class ActorBreakdownOut(BaseModel):
    actor: str
    reads: int
    writes: int


class MemoryStatsOut(BaseModel):
    total_memories: int
    total_reads: int
    total_writes: int
    never_read: int
    # 模型想读但不存在的路径次数。偏高说明索引和实际内容对不上。
    missed_reads: int
    daily: list[DailyActivityOut]
    top: list[MemoryUsageOut]
    unused: list[MemoryUsageOut]
    by_actor: list[ActorBreakdownOut]


def _store(session: AsyncSession) -> MemoryStore:
    """经由 API 的改动都算人工编辑，和模型自己写的区分开。

    track_reads=False：你在记忆页翻看不等于「模型用了这条记忆」，
    统计进去会让使用率完全失真。
    """
    return MemoryStore(session, actor="manual", track_reads=False)


def _api_path(raw: str) -> str:
    """URL 里的 path 是不带 /memories 前缀的相对路径，这里补回去。"""
    return f"{MEMORY_ROOT}/{raw.lstrip('/')}"


@router.get("", response_model=list[MemoryNodeOut])
async def get_tree(session: AsyncSession = Depends(get_session)) -> list[Any]:
    return await _store(session).tree()


@router.get("/stats", response_model=MemoryStatsOut)
async def get_stats(
    days: int = 30,
    top: int = 10,
    session: AsyncSession = Depends(get_session),
) -> Any:
    """记忆使用率总览。一次返回仪表盘需要的全部数据，前端一个 loading 态就够。

    和 ``/versions`` 一样，必须声明在 ``/{path:path}`` 之前。
    """
    return await collect_stats(session, days=days, top_n=top)


@router.post("/restore", response_model=MemoryOut)
async def restore_version(
    payload: RestoreRequest,
    session: AsyncSession = Depends(get_session),
) -> Any:
    """把某个历史版本的内容写回它自己的路径。

    按 version_id 而不是路径来定位，是因为**已删除的记忆也要能恢复** ——
    文件删掉后路径不在树里，按路径根本构造不出请求。

    恢复本身也会记一条新版本（actor=manual），所以回滚可以再回滚。
    """
    version = await session.get(MemoryVersion, payload.version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "版本不存在")

    store = _store(session)
    async with _as_http_error():
        await store.create(version.path, version.content)
        logger.info("↺ 恢复 %s 到版本 #%s", version.path, version.id)
        return await store.read(version.path)


@router.get("/versions", response_model=list[MemoryVersionOut])
async def list_all_versions(
    day: dt.date | None = None,
    actor: str | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> list[MemoryVersion]:
    """全局记忆变更流：某天所有记忆改了什么、是谁改的。

    必须声明在 ``/{path:path}/versions`` **之前** —— path 是贪婪匹配，
    顺序反了这个路由永远不会命中（tests/test_memory_api.py 钉住了这一点）。

    这也是查看**已删除记忆**历史的唯一入口：文件删了之后路径就不在树里，
    按路径查的那个接口再也构造不出 URL。
    """
    stmt = (
        select(MemoryVersion)
        .order_by(MemoryVersion.created_at.desc(), MemoryVersion.id.desc())
        .limit(min(limit, 500))
    )
    if day is not None:
        start, end = local_day_bounds(day)
        stmt = stmt.where(
            MemoryVersion.created_at >= start, MemoryVersion.created_at < end
        )
    if actor is not None:
        stmt = stmt.where(MemoryVersion.actor == actor)
    return list((await session.execute(stmt)).scalars())


@router.get("/{path:path}/versions", response_model=list[MemoryVersionOut])
async def get_versions(
    path: str, limit: int = 50, session: AsyncSession = Depends(get_session)
) -> list[MemoryVersion]:
    stmt = (
        select(MemoryVersion)
        .where(MemoryVersion.path == _api_path(path))
        .order_by(MemoryVersion.created_at.desc(), MemoryVersion.id.desc())
        .limit(min(limit, 200))
    )
    return list((await session.execute(stmt)).scalars())


@router.get("/{path:path}", response_model=MemoryOut)
async def get_memory(path: str, session: AsyncSession = Depends(get_session)) -> Any:
    async with _as_http_error():
        return await _store(session).read(_api_path(path))


@router.put("/{path:path}", response_model=MemoryOut)
async def put_memory(
    path: str,
    payload: MemoryWrite,
    session: AsyncSession = Depends(get_session),
) -> Any:
    store = _store(session)
    target = _api_path(path)
    async with _as_http_error():
        await store.create(target, payload.content)
        logger.info("✎ 手动编辑 %s (%d 字符)", target, len(payload.content))
        return await store.read(target)


@router.delete("/{path:path}", status_code=204)
async def delete_memory(
    path: str, session: AsyncSession = Depends(get_session)
) -> None:
    async with _as_http_error():
        await _store(session).delete(_api_path(path))
        logger.warning("✁ 手动删除 %s", _api_path(path))


@asynccontextmanager
async def _as_http_error() -> AsyncIterator[None]:
    """把记忆层异常翻译成 HTTP 状态码。"""
    try:
        yield
    except MemoryNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvalidMemoryPath as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except MemoryToolError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
