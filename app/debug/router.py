"""调试接口：看清每次到底发了什么给模型。

三个入口，覆盖三种问法：

* 「这个会话现在的 system prompt 长什么样」→ ``/prompt``（不用发消息就能看）
* 「刚才那轮请求发了什么」→ ``/requests`` 列表 + ``/requests/{id}`` 完整 payload
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.debug.recorder import recorder
from app.memory.prompt import build_system_prompt
from app.memory.store import MemoryStore
from app.security import require_api_key
from app.settings_store import resolve_settings

router = APIRouter(
    prefix="/api/debug", tags=["debug"], dependencies=[Depends(require_api_key)]
)


@router.get("/requests")
async def list_requests(
    conversation_id: int | None = None,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """最近发出的请求，新的在前。

    ``enabled`` 为 false 时列表一定是空的 —— 不是没请求过，是没在记。
    界面要把这两种情况分开显示，否则会以为功能坏了。
    """
    settings = await resolve_settings(session)
    items = recorder.list(conversation_id=conversation_id, limit=limit)
    return {
        "enabled": settings.debug_prompts,
        "capacity": recorder.capacity,
        "items": [s.summary() for s in items],
    }


@router.get("/requests/{snapshot_id}")
async def get_request(snapshot_id: int) -> dict[str, Any]:
    """完整请求体，原样返回 —— 这就是发给模型的那个 JSON。"""
    snapshot = recorder.get(snapshot_id)
    if snapshot is None:
        # 环形缓冲只留最近 20 条，翻旧的会落到这里
        raise HTTPException(status.HTTP_404_NOT_FOUND, "快照已经被冲掉了")
    return snapshot.detail()


@router.delete("/requests", status_code=status.HTTP_204_NO_CONTENT)
async def clear_requests() -> None:
    recorder.clear()


@router.get("/prompt")
async def current_prompt(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """当前会**原样**进入 system prompt 的那段文本。

    注意它只含记忆**索引**（MEMORY.md），不含具体记忆文件的正文 ——
    那些要模型用 view 读进来才会出现在上下文里。这是渐进式披露的核心，
    也是最容易误解的地方。
    """
    settings = await resolve_settings(session)
    system = await build_system_prompt(MemoryStore(session, actor="manual"), settings)
    return {
        "system": system,
        "chars": len(system),
        # 粗略估算，中文约 1 字 1 token，英文更少。用来判断量级，不是精确计费。
        "approx_tokens": len(system),
        "note": "只含记忆索引；具体记忆正文要模型 view 之后才进上下文",
    }
