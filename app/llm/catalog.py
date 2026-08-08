"""模型服务与模型档案目录。

模型服务描述「请求发到哪里」，模型档案描述「具体调用哪个模型」。
密钥只通过 ``credential_ref`` 指向环境变量，不进入数据库和 API 响应。
"""

from __future__ import annotations

import os
from typing import Any, Literal

from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import ModelProfile, ModelService
from app.settings_store import resolve_settings
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


# credential_ref 只能指向**外部模型服务的密钥**。
# 判据是名字形状 + 一份基础设施黑名单，三个来源（Settings / 环境变量 / .env）统一适用。
CREDENTIAL_SUFFIXES = ("_API_KEY", "_KEY", "_TOKEN", "_SECRET")
# 应用自身的基础设施，形状上像密钥但绝不该被当成模型服务凭据发出去
INTERNAL_REFS = frozenset({"API_KEY", "DATABASE_URL"})


def is_credential_ref(ref: str) -> bool:
    """这个引用名可以被当成模型服务的密钥读取吗。

    **三个来源都要过这一关**。只挡 Settings 是不够的：`.env` 里同样有
    `DATABASE_URL`，光挡住 `getattr` 那条路，连接串照样会从 dotenv 那条路漏出去。
    """
    return (
        bool(ref)
        and ref not in INTERNAL_REFS
        and ref.endswith(CREDENTIAL_SUFFIXES)
    )


def _secret(ref: str, settings: Settings) -> str:
    """读取凭据引用。

    容器运行时优先从环境变量取；宿主机直接运行时补读本地 ``.env``。
    绝不把读到的值放入模型目录响应。

    ⚠️ **只认密钥形状的引用名**（见 `is_credential_ref`）。原来任何全大写下划线的
    名字都合法，于是 `DATABASE_URL` 是个合法引用，会把**带密码的数据库连接串**
    当成 api key 发到该服务的 `base_url` 指向的第三方，界面上还显示「凭据已配置 ✓」。
    更常见的无害版本是引用名打错、恰好撞上某个已存在的变量，于是拿到一个错的值
    而不是干脆报「未配置」。
    """
    if not is_credential_ref(ref):
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


async def _get_or_create(session: AsyncSession, model, slug: str, build):
    """按 slug 取，没有就建 —— 并发安全。

    原来是裸的 SELECT-then-INSERT，而 `slug` 上有唯一索引。首次访问时前端会同时打
    好几个接口（聊天页拉模型目录、设置页拉配置……），几个请求同时发现「没有这条」
    然后一起 INSERT，**一个成功、其余全 500**。迁移完第一次打开界面就会撞上，
    第二次之后正常，所以在开发时极容易看不见。

    用 savepoint 兜住冲突：撞了就说明别人刚建好，重新读一次即可。
    """
    existing = await session.scalar(select(model).where(model.slug == slug))
    if existing is not None:
        return existing
    try:
        async with session.begin_nested():
            created = build()
            session.add(created)
            await session.flush()
        return created
    except IntegrityError:
        return await session.scalar(select(model).where(model.slug == slug))


async def ensure_builtin_catalog(
    session: AsyncSession, settings: Settings | None = None
) -> None:
    """把现有两个 provider 映射成目录记录，兼容已有部署。"""
    settings = settings or get_settings()
    for definition in BUILTIN_SERVICES:
        slug = definition["slug"]
        model_id, prefix, base_url = _builtin_model(settings, slug)
        service = await _get_or_create(
            session,
            ModelService,
            slug,
            lambda: ModelService(
                slug=definition["slug"],
                name=definition["name"],
                protocol=definition["protocol"],
                base_url=base_url,
                credential_ref=definition["credential_ref"],
                config={"managed_by_runtime": True},
            ),
        )
        if (service.config or {}).get("managed_by_runtime", False):
            service.base_url = base_url
            service.credential_ref = definition["credential_ref"]

        profile_slug = f"builtin:{slug}"
        capabilities = dict(DEFAULT_CAPABILITIES)
        if slug == "anthropic":
            capabilities.update({"thinking": True, "json_mode": True})
        else:
            capabilities.update({"thinking": settings.deepseek_thinking})
        profile = await _get_or_create(
            session,
            ModelProfile,
            profile_slug,
            lambda: ModelProfile(
                service_id=service.id,
                slug=profile_slug,
                model_id=model_id,
                display_name=f"{prefix} · {model_id}",
                capabilities=capabilities,
                options={"managed_by_runtime": True},
            ),
        )
        if (profile.options or {}).get("managed_by_runtime", False):
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
    """解析会话/全局/旧配置，返回一个可执行的模型目标。

    不传 `settings` 时从**数据库**解析生效配置，而不是退回 `.env` 启动快照 ——
    默认模型正是存在 `app_settings` 里的，用快照会看不见它。
    """
    settings = settings or await resolve_settings(session)
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
    """模型目录，含「当前默认是哪个」。

    ⚠️ 缺省时必须走 `resolve_settings`（数据库覆盖叠加在 .env 之上），
    不能退回 `get_settings()` 的启动快照 —— `chat_model_profile_id` 就存在数据库里。
    用快照的症状是：在界面上换默认模型、保存成功、刷新一下又变回去了
    （写进去了，但读的是另一个地方）。
    """
    settings = settings or await resolve_settings(session)
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
