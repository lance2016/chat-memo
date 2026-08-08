from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelProfile, ModelService
from app.db.session import get_session
from app.llm.catalog import (
    DEFAULT_CAPABILITIES,
    is_credential_ref,
    catalog_payload,
    ensure_builtin_catalog,
    resolve_model_target,
)
from app.security import require_api_key
from app.settings_store import apply, resolve_settings

router = APIRouter(
    prefix="/api/models",
    tags=["models"],
    dependencies=[Depends(require_api_key)],
)


class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,79}$")
    protocol: Literal["anthropic", "openai_compatible"]
    base_url: str = Field(default="", max_length=500)
    # 只保存变量名，例如 OPENROUTER_API_KEY；不接受真正的 key。
    credential_ref: str = Field(default="", max_length=120, pattern=r"^$|^[A-Z][A-Z0-9_]*$")


class ServicePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    credential_ref: str | None = Field(default=None, max_length=120, pattern=r"^$|^[A-Z][A-Z0-9_]*$")
    enabled: bool | None = None


class ProfileCreate(BaseModel):
    service_id: int
    model_id: str = Field(min_length=1, max_length=240)
    display_name: str | None = Field(default=None, max_length=160)
    capabilities: dict[str, bool] = Field(default_factory=dict)


class ProfilePatch(BaseModel):
    model_id: str | None = Field(default=None, min_length=1, max_length=240)
    display_name: str | None = Field(default=None, max_length=160)
    capabilities: dict[str, bool] | None = None
    enabled: bool | None = None


class DefaultModelRequest(BaseModel):
    purpose: Literal["chat", "consolidation"] = "chat"
    profile_id: int | None = None


def _check_credential_ref(ref: str) -> None:
    """凭据引用必须长得像密钥。

    挡在写入这一步，用户当场看到原因；否则要等到发第一条消息才发现「未配置凭据」，
    或者更糟 —— 引用了 `DATABASE_URL` 这种东西而它恰好有值。
    """
    ref = (ref or "").strip()
    if ref and not is_credential_ref(ref):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"凭据引用 {ref} 不像密钥变量名，应以 _API_KEY / _KEY / _TOKEN / _SECRET 结尾，"
            "且不能指向应用自身的 API_KEY 或 DATABASE_URL",
        )


@router.get("")
async def list_models(
    purpose: Literal["chat", "consolidation"] = "chat",
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await catalog_payload(session, purpose=purpose)


@router.post("/services", status_code=status.HTTP_201_CREATED)
async def create_service(
    payload: ServiceCreate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_builtin_catalog(session)
    if await session.scalar(select(ModelService).where(ModelService.slug == payload.slug)):
        raise HTTPException(status.HTTP_409_CONFLICT, "模型服务 slug 已存在")
    _check_credential_ref(payload.credential_ref)
    session.add(
        ModelService(
            name=payload.name.strip(),
            slug=payload.slug,
            protocol=payload.protocol,
            base_url=payload.base_url.strip().rstrip("/"),
            credential_ref=payload.credential_ref.strip(),
            config={"managed_by_runtime": False},
        )
    )
    await session.flush()
    return await catalog_payload(session)


@router.patch("/services/{service_id}")
async def update_service(
    service_id: int,
    payload: ServicePatch,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    service = await session.get(ModelService, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型服务不存在")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("credential_ref") is not None:
        _check_credential_ref(changes["credential_ref"])
    for key, value in changes.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(service, key, value)
    service.config = {**(service.config or {}), "managed_by_runtime": False}
    await session.flush()
    return await catalog_payload(session)


@router.post("/services/{service_id}/test")
async def test_service(
    service_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """检查连接配置是否具备调用条件。

    这里不主动发送计费请求；真正的网络连通性会在第一次模型调用时验证。
    """
    settings = await resolve_settings(session)
    service = await session.get(ModelService, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型服务不存在")
    profiles = list(
        (
            await session.execute(
                select(ModelProfile).where(ModelProfile.service_id == service_id)
            )
        ).scalars()
    )
    if service.credential_ref:
        try:
            await resolve_model_target(
                session,
                settings,
                profile_id=profiles[0].id if profiles else None,
            )
        except ValueError as exc:
            return {"ok": False, "service_id": service_id, "detail": str(exc)}
    return {
        "ok": bool(service.enabled and profiles),
        "service_id": service_id,
        "profiles": len(profiles),
        "detail": "配置已就绪；发送一条消息即可验证实际接口" if profiles else "请先添加模型",
    }


@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_profile(
    payload: ProfileCreate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    service = await session.get(ModelService, payload.service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型服务不存在")
    duplicate = await session.scalar(
        select(ModelProfile).where(
            ModelProfile.service_id == payload.service_id,
            ModelProfile.model_id == payload.model_id.strip(),
        )
    )
    if duplicate is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "这个模型已经添加")
    slug = f"{service.slug}:{payload.model_id.strip()}"
    existing_slug = await session.scalar(select(ModelProfile).where(ModelProfile.slug == slug))
    if existing_slug is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "模型档案 slug 已存在")
    capabilities = {**DEFAULT_CAPABILITIES, **payload.capabilities}
    if service.protocol == "anthropic":
        capabilities.update({"thinking": True, "json_mode": True})
    session.add(
        ModelProfile(
            service_id=service.id,
            slug=slug,
            model_id=payload.model_id.strip(),
            display_name=(payload.display_name or payload.model_id).strip(),
            capabilities=capabilities,
            options={"managed_by_runtime": False},
        )
    )
    await session.flush()
    return await catalog_payload(session)


@router.patch("/profiles/{profile_id}")
async def update_profile(
    profile_id: int,
    payload: ProfilePatch,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    profile = await session.get(ModelProfile, profile_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型档案不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, key, value.strip() if isinstance(value, str) else value)
    profile.options = {**(profile.options or {}), "managed_by_runtime": False}
    await session.flush()
    return await catalog_payload(session)


@router.delete("/profiles/{profile_id}")
async def disable_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    profile = await session.get(ModelProfile, profile_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型档案不存在")
    profile.enabled = False
    await session.flush()
    return await catalog_payload(session)


@router.post("/default")
async def set_default_model(
    payload: DefaultModelRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    settings = await resolve_settings(session)
    if payload.profile_id is not None:
        try:
            target = await resolve_model_target(
                session, settings, profile_id=payload.profile_id, purpose=payload.purpose
            )
        except ValueError as exc:
            # 选一个没配凭据/已停用的模型，要给出能照着做的 400，
            # 而不是让 resolve 的 ValueError 冒成 500
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        if payload.purpose == "chat" and not target.capabilities.get("tool_calling", False):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "聊天默认模型必须支持工具调用")
    key = (
        "chat_model_profile_id"
        if payload.purpose == "chat"
        else "consolidate_model_profile_id"
    )
    await apply(session, {key: payload.profile_id}, settings)
    await session.flush()
    return await catalog_payload(
        session, settings=await resolve_settings(session), purpose=payload.purpose
    )
