"""可观测性状态接口。

只读、不做任何模型调用，可以随便刷。启用与否是启动期决定的，
所以这里没有写接口 —— 见 `app/obs/status.py` 的模块说明。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.obs.status import observability_status
from app.security import require_api_key
from app.settings_store import resolve_settings

router = APIRouter(
    prefix="/api/obs", tags=["observability"], dependencies=[Depends(require_api_key)]
)


@router.get("/status")
async def get_status(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Phoenix 现在到底能不能用，以及卡在哪一步。"""
    return await observability_status(await resolve_settings(session))
