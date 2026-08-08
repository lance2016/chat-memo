"""模型服务与模型档案目录。

模型服务描述「请求发到哪里」，模型档案描述「具体调用哪个模型」。
密钥只通过 ``credential_ref`` 指向环境变量，不进入数据库和 API 响应。
"""

from __future__ import annotations

import os
from typing import Any, Literal

from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import ModelProfile, ModelService
from app.llm.target import (
    DEFAULT_CAPABILITIES,
    ModelTarget,
)


BUILTIN_SERVICES = (
    {
        "slug": "anthropic",
        "name": "Anthropic",
        "protocol": "anthropic",
        "credential_ref": "ANTHROPIC_API_KEY",
    },
    {
        "slug": "deepseek",
        "name": "DeepSeek",
        "protocol": "openai_compatible",
        "credential_ref": "DEEPSEEK_API_KEY",
    },
)


def _secret(ref: str, settings: Settings) -> str:
    """读取凭据引用。

    容器运行时优先从环境变量取；宿主机直接运行时补读本地 ``.env``。
    绝不把读到的值放入模型目录响应。
    """
    if not ref:
        return ""
    value = getattr(settings, ref.lower(), "")
    if value:
        return str(value)
    value = os.environ.get(ref, "")
    if value:
        return value
    try:
        return str(dotenv_values(".env").get(ref) or "")
    except OSError:
        return ""


def _builtin_model(settings: Settings, service_slug: str) -> tuple[str, str, str]:
    if service_slug == "anthropic":
        return settings.model, "Claude", ""
    return settings.deepseek_model, "DeepSeek", settings.deepseek_base_url


async def ensure_builtin_catalog(
    session: AsyncSession, settings: Settings | None = None
) -> None:
    """把现有两个 provider 映射成目录记录，兼容已有部署。"""
    settings = settings or get_settings()
    for definition in BUILTIN_SERVICES:
        slug = definition["slug"]
        service = await session.scalar(
            select(ModelService).where(ModelService.slug == slug)
        )
        model_id, prefix, base_url = _builtin_model(settings, slug)
        if service is None:
            service = ModelService(
                slug=slug,
                name=definition["name"],
                protocol=definition["protocol"],
                base_url=base_url,
                credential_ref=definition["credential_ref"],
                config={"managed_by_runtime": True},
            )
            session.add(service)
            await session.flush()
        elif (service.config or {}).get("managed_by_runtime", False):
            service.base_url = base_url
            service.credential_ref = definition["credential_ref"]

        profile_slug = f"builtin:{slug}"
        profile = await session.scalar(
            select(ModelProfile).where(ModelProfile.slug == profile_slug)
        )
        if profile is None:
            capabilities = dict(DEFAULT_CAPABILITIES)
            if slug == "anthropic":
                capabilities.update({"thinking": True, "json_mode": True})
            else:
                capabilities.update({"thinking": settings.deepseek_thinking})
            profile = ModelProfile(
                service_id=service.id,
                slug=profile_slug,
                model_id=model_id,
                display_name=f"{prefix} · {model_id}",
                capabilities=capabilities,
                options={"managed_by_runtime": True},
            )
            session.add(profile)
        elif (profile.options or {}).get("managed_by_runtime", False):
            profile.model_id = model_id
            profile.display_name = f"{prefix} · {model_id}"


def _capabilities(profile: ModelProfile, protocol: str) -> dict[str, bool]:
    result = dict(DEFAULT_CAPABILITIES)
    if protocol == "anthropic":
        result.update({"thinking": True, "json_mode": True})
    result.update({key: bool(value) for key, value in (profile.capabilities or {}).items()})
    return result


def _target_from_rows(
    service: ModelService, profile: ModelProfile, settings: Settings
) -> ModelTarget:
    protocol = service.protocol
    if protocol not in {"anthropic", "openai_compatible"}:
        raise ValueError(f"暂不支持模型服务协议 {protocol!r}")
    api_key = _secret(service.credential_ref, settings)
    if service.credential_ref and not api_key:
        raise ValueError(
            f"模型服务 {service.name} 未配置凭据 {service.credential_ref}"
        )
    capabilities = _capabilities(profile, protocol)
    # 调用参数优先取档案自己的 options；没填就落回该协议的旧全局配置，
    # 这样老部署升级上来行为不变，而新加的模型可以逐个调。
    options = profile.options or {}
    fallback = ModelTarget.from_settings(
        settings.model_copy(
            update={"provider": "anthropic" if protocol == "anthropic" else "deepseek"}
        )
    )
    return ModelTarget(
        profile_id=profile.id,
        service_slug=service.slug,
        service_name=service.name,
        protocol=protocol,
        model_id=profile.model_id,
        display_name=profile.display_name,
        base_url=service.base_url,
        api_key=api_key,
        capabilities=capabilities,
        max_tokens=int(options.get("max_tokens") or fallback.max_tokens),
        thinking_default=bool(
            options.get("thinking", capabilities.get("thinking", False))
        ),
        effort=str(options.get("effort") or fallback.effort),
    )


async def resolve_model_target(
    session: AsyncSession,
    settings: Settings | None = None,
    *,
    profile_id: int | None = None,
    purpose: Literal["chat", "consolidation"] = "chat",
    legacy_model_id: str = "",
) -> ModelTarget:
    """解析会话/全局/旧配置，返回一个可执行的模型目标。"""
    settings = settings or get_settings()
    await ensure_builtin_catalog(session, settings)

    if profile_id is None:
        profile_id = (
            settings.chat_model_profile_id
            if purpose == "chat"
            else settings.consolidate_model_profile_id
        )

    if profile_id is not None:
        row = await session.execute(
            select(ModelService, ModelProfile)
            .join(ModelProfile, ModelProfile.service_id == ModelService.id)
            .where(ModelProfile.id == profile_id)
        )
        pair = row.first()
        if pair is None:
            raise ValueError(f"模型档案 #{profile_id} 不存在")
        service, profile = pair
        if not service.enabled or not profile.enabled:
            raise ValueError(f"模型档案「{profile.display_name}」已停用")
        return _target_from_rows(service, profile, settings)

    # 旧配置对应的内置服务与模型，判断只在 from_settings 里做一次。
    legacy = ModelTarget.from_settings(settings, legacy_model_id)
    legacy_slug, legacy_model = legacy.service_slug, legacy.model_id
    builtin_pair = (
        await session.execute(
            select(ModelService, ModelProfile)
            .join(ModelProfile, ModelProfile.service_id == ModelService.id)
            .where(
                ModelService.slug == legacy_slug,
                ModelProfile.model_id == legacy_model,
            )
        )
    ).first()
    if builtin_pair is not None:
        service, profile = builtin_pair
        return _target_from_rows(service, profile, settings)

    # 目录里没有对应记录（老部署刚升级、或用户手工删过），退回旧配置。
    # 分支在 `ModelTarget.from_settings` 里，这里不再重复一遍厂商判断。
    return ModelTarget.from_settings(settings, legacy_model_id)


def _legacy_default_profile_id(
    profiles: list[tuple[ModelService, ModelProfile]], settings: Settings
) -> int | None:
    if settings.chat_model_profile_id is not None:
        return settings.chat_model_profile_id
    legacy = ModelTarget.from_settings(settings)
    slug, model_id = legacy.service_slug, legacy.model_id
    for service, profile in profiles:
        if service.slug == slug and profile.model_id == model_id:
            return profile.id
    return None


async def catalog_payload(
    session: AsyncSession, settings: Settings | None = None, purpose: str = "chat"
) -> dict[str, Any]:
    settings = settings or get_settings()
    await ensure_builtin_catalog(session, settings)
    services_rows = list((await session.execute(select(ModelService).order_by(ModelService.name))).scalars())
    rows = list(
        (
            await session.execute(
                select(ModelService, ModelProfile)
                .join(ModelProfile, ModelProfile.service_id == ModelService.id)
                .order_by(ModelService.name, ModelProfile.display_name)
            )
        ).all()
    )
    if purpose == "chat":
        default_id = _legacy_default_profile_id(rows, settings)
    elif settings.consolidate_model_profile_id is not None:
        default_id = settings.consolidate_model_profile_id
    else:
        legacy = ModelTarget.from_settings(settings, settings.consolidate_model)
        slug, legacy_model = legacy.service_slug, legacy.model_id
        default_id = next(
            (
                profile.id
                for service, profile in rows
                if service.slug == slug and profile.model_id == legacy_model
            ),
            None,
        )
    profiles: list[dict[str, Any]] = []
    for service, profile in rows:
        capabilities = _capabilities(profile, service.protocol)
        reason = ""
        available = bool(service.enabled and profile.enabled)
        if not service.enabled:
            reason = "模型服务已停用"
        elif not profile.enabled:
            reason = "模型已停用"
        elif service.credential_ref and not _secret(service.credential_ref, settings):
            available = False
            reason = f"未配置 {service.credential_ref}"
        elif purpose == "chat" and not capabilities.get("tool_calling", False):
            available = False
            reason = "聊天需要支持工具调用"
        profiles.append(
            {
                "id": profile.id,
                "slug": profile.slug,
                "service_id": service.id,
                "service_slug": service.slug,
                "service_name": service.name,
                "protocol": service.protocol,
                "model_id": profile.model_id,
                "display_name": profile.display_name,
                "enabled": profile.enabled,
                "available": available,
                "reason": reason,
                "capabilities": capabilities,
                "is_default": profile.id == default_id,
            }
        )
    services = [
        {
            "id": service.id,
            "slug": service.slug,
            "name": service.name,
            "protocol": service.protocol,
            "base_url": service.base_url,
            "credential_configured": bool(
                not service.credential_ref or _secret(service.credential_ref, settings)
            ),
            "enabled": service.enabled,
        }
        for service in services_rows
    ]
    return {
        "purpose": purpose,
        "default_profile_id": default_id,
        "services": services,
        "profiles": profiles,
    }
