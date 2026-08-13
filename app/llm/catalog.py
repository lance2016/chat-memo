"""模型服务与模型档案目录。

模型服务描述「请求发到哪里」，模型档案描述「具体调用哪个模型」。
密钥只通过 ``credential_ref`` 指向环境变量，不进入数据库和 API 响应。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import ModelProfile, ModelService
from app.llm.target import (
    DEFAULT_CAPABILITIES,
    ModelTarget,
    thinking_efforts_for,
)
from app.settings_store import is_configured, resolve_settings

logger = logging.getLogger(__name__)

# 「解析哪个模型档案」的用途。比 `app.agent.Purpose`（决定带哪些工具）多出
# `title` / `vision` —— 标题不跑 agent loop，看图也有独立的视觉链路，所以两者不该
# 复用同一个类型。
TargetPurpose = Literal["chat", "consolidation", "title", "vision"]

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
    {
        "slug": "openai-codex",
        "name": "OpenAI via Codex",
        "protocol": "openai_responses",
        # 本地代理默认不要求入站 API key；若用户给代理设置了 --api-key，
        # 可在模型服务目录里把 credential_ref 改成 OPENAI_API_KEY。
        "credential_ref": "",
    },
)

# DeepSeek 的 V4 Flash / Pro 共用同一个 Chat Completions 接口和请求方言。
# ``deepseek_model`` 仍决定旧配置的默认模型；这里额外登记在售型号，让用户能在
# 模型目录里直接切换，而不需要手工新建一份完全相同的服务配置。
DEEPSEEK_BUILTIN_MODELS = (
    "deepseek-v4-flash",
    "deepseek-v4-pro",
)


def _openai_model_ids(settings: Settings) -> tuple[str, ...]:
    """返回内置 OpenAI 服务的模型清单，并把全局默认模型排在第一位。"""
    model_ids = [item.strip() for item in settings.openai_models.split(",") if item.strip()]
    if settings.openai_model:
        model_ids = [item for item in model_ids if item != settings.openai_model]
        model_ids.insert(0, settings.openai_model)
    # 保持配置文件即使被误填重复模型时也只建一个档案。
    return tuple(dict.fromkeys(model_ids))


def _deepseek_model_ids(settings: Settings) -> tuple[str, ...]:
    """返回内置 DeepSeek 型号，并把旧配置选择的模型排在第一位。"""
    model_ids = [settings.deepseek_model, *DEEPSEEK_BUILTIN_MODELS]
    return tuple(dict.fromkeys(model_id for model_id in model_ids if model_id))


def _builtin_capabilities(protocol: str, model_id: str) -> dict[str, bool]:
    capabilities = dict(DEFAULT_CAPABILITIES)
    if protocol == "anthropic":
        capabilities.update({"thinking": True, "json_mode": True, "vision": True})
    elif protocol == "openai_responses":
        capabilities.update({"thinking": True, "json_mode": True, "vision": True})
        # image 模型属于生成图片的接口，不应被聊天 agent 当作普通文本模型调用。
        if model_id.startswith("gpt-image-"):
            capabilities.update(
                {
                    "streaming": False,
                    "tool_calling": False,
                    "text_generation": False,
                    "thinking": False,
                    "vision": False,
                    "json_mode": False,
                }
            )
    else:
        # 同 target.from_settings：这里是「会不会思考」，不是「默认要不要思考」。
        capabilities.update({"thinking": True})
    return capabilities


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
    for value in (
        getattr(settings, ref.lower(), ""),
        os.environ.get(ref, ""),
        _from_dotenv(ref),
    ):
        # `.env.example` 的占位符（`sk-ant-...`）不算配置好 —— 判据和设置页那边
        # 共用 `is_configured`。两处各写一套的后果实测过：模型目录说「凭据已配置」、
        # 界面把 Claude 列成可用，选中之后发送时才 401。
        if is_configured(value):
            return str(value)
    return ""


def _from_dotenv(ref: str) -> str:
    """宿主机直接跑后端时补读本地 `.env`（容器里走环境变量）。"""
    try:
        return str(dotenv_values(".env").get(ref) or "")
    except OSError:
        return ""


def _builtin_model(settings: Settings, service_slug: str) -> tuple[str, str, str]:
    if service_slug == "anthropic":
        return settings.model, "Claude", ""
    if service_slug == "openai-codex":
        return settings.openai_model, "OpenAI Responses", settings.openai_base_url
    return settings.deepseek_model, "DeepSeek", settings.deepseek_base_url


async def _get_or_create(session: AsyncSession, model, slug: str, **values):
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
            # 直接收字段而不是收一个工厂闭包：闭包在 for 里捕获的是循环变量本身，
            # 现在调用是同步的所以碰巧没事，但谁把调用延后一点就会悄悄拿到最后一轮的值。
            created = model(slug=slug, **values)
            session.add(created)
            await session.flush()
        return created
    except IntegrityError:
        return await session.scalar(select(model).where(model.slug == slug))


async def ensure_builtin_catalog(
    session: AsyncSession, settings: Settings | None = None
) -> None:
    """把内置 provider 映射成目录记录，兼容已有部署。"""
    settings = settings or get_settings()
    for definition in BUILTIN_SERVICES:
        slug = definition["slug"]
        model_id, prefix, base_url = _builtin_model(settings, slug)
        service = await _get_or_create(
            session,
            ModelService,
            slug,
            name=definition["name"],
            protocol=definition["protocol"],
            base_url=base_url,
            credential_ref=definition["credential_ref"],
            config={"managed_by_runtime": True},
        )
        if (service.config or {}).get("managed_by_runtime", False):
            service.base_url = base_url
            service.credential_ref = definition["credential_ref"]
            # 这个内置服务只在用户提供了 OPENAI_BASE_URL 时可用；避免没有配置时
            # 意外回落到 api.openai.com，也让模型目录给出明确的不可用状态。
            if slug == "openai-codex":
                service.enabled = bool(base_url)

        if slug == "openai-codex":
            model_ids = _openai_model_ids(settings)
        elif slug == "deepseek":
            model_ids = _deepseek_model_ids(settings)
        else:
            model_ids = (model_id,)
        managed_profiles = [
            profile
            for profile in (
                await session.scalars(
                    select(ModelProfile).where(ModelProfile.service_id == service.id)
                )
            ).all()
            if (profile.options or {}).get("managed_by_runtime", False)
        ]
        existing_profiles = {profile.model_id: profile for profile in managed_profiles}
        primary_profile_slug = f"builtin:{slug}"
        primary_profile_exists = any(
            profile.slug == primary_profile_slug for profile in managed_profiles
        )
        for index, current_model_id in enumerate(model_ids):
            # 兼容此前每个内置服务只创建一个 ``builtin:<service>`` 档案的版本；
            # 后续模型把 model id 放进 slug。旧部署在新增型号成为 legacy 默认时，
            # 主 slug 仍被旧型号占用，不能把旧记录原地改名后又在下一轮改回来。
            profile_slug = (
                primary_profile_slug
                if index == 0 and not primary_profile_exists
                else f"builtin:{slug}:{current_model_id}"
            )
            profile = existing_profiles.get(current_model_id)
            if profile is None:
                profile = await _get_or_create(
                    session,
                    ModelProfile,
                    profile_slug,
                    service_id=service.id,
                    model_id=current_model_id,
                    display_name=f"{prefix} · {current_model_id}",
                    capabilities=_builtin_capabilities(definition["protocol"], current_model_id),
                    options={"managed_by_runtime": True},
                )
                existing_profiles[current_model_id] = profile
                primary_profile_exists = (
                    primary_profile_exists or profile.slug == primary_profile_slug
                )
            if (profile.options or {}).get("managed_by_runtime", False):
                profile.model_id = current_model_id
                profile.display_name = f"{prefix} · {current_model_id}"
                # ⚠️ 能力也要跟着代码走，不能只同步名字。`_capabilities` 是「协议默认
                # 打底、档案存的值覆盖在上面」，所以内置档案里一个过期的 `vision: false`
                # 会**永久压住**新加的能力。用户自己加的档案不走这里，他们的勾选不受影响。
                profile.capabilities = _builtin_capabilities(definition["protocol"], current_model_id)


def _capabilities(profile: ModelProfile, protocol: str) -> dict[str, bool]:
    result = dict(DEFAULT_CAPABILITIES)
    if protocol == "anthropic":
        result.update({"thinking": True, "json_mode": True, "vision": True})
    elif protocol == "openai_responses":
        result.update({"thinking": True, "json_mode": True, "vision": True})
    result.update({key: bool(value) for key, value in (profile.capabilities or {}).items()})
    return result


def _thinking_effort_config(
    protocol: str,
    service_slug: str,
    capabilities: dict[str, bool],
    options: dict[str, Any],
    fallback_effort: str,
) -> tuple[tuple[str, ...], str | None]:
    """Resolve effort metadata without inspecting model names.

    Protocol defaults describe what our provider implementation can enforce.
    A profile can narrow them with ``options.thinking_efforts`` when a model
    supports fewer levels. Unknown values are discarded instead of being sent
    optimistically to an upstream API.
    """
    if not capabilities.get("thinking", False):
        return (), None
    allowed = thinking_efforts_for(protocol, service_slug)
    configured = options.get("thinking_efforts")
    if isinstance(configured, list):
        efforts = tuple(
            value
            for value in dict.fromkeys(str(item) for item in configured)
            if value in allowed
        )
    else:
        efforts = allowed
    if not efforts:
        return (), None
    requested = str(
        options.get("thinking_effort_default")
        or options.get("effort")
        or fallback_effort
        or ""
    )
    default = requested if requested in efforts else "medium" if "medium" in efforts else efforts[0]
    return efforts, default


def _target_from_rows(
    service: ModelService, profile: ModelProfile, settings: Settings
) -> ModelTarget:
    protocol = service.protocol
    if protocol not in {"anthropic", "openai_compatible", "openai_responses"}:
        raise ValueError(f"暂不支持模型服务协议 {protocol!r}")
    api_key = _secret(service.credential_ref, settings)
    if protocol == "openai_responses" and service.slug == "openai-codex":
        # 本地代理默认不校验 key，但若用户用 --api-key 保护了它，仍允许通过
        # OPENAI_API_KEY 传入同一个值。两种模式都不应让模型目录判定为不可用。
        api_key = settings.openai_api_key or "not-needed"
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
            update={
                "provider": (
                    "anthropic"
                    if protocol == "anthropic"
                    else "openai"
                    if protocol == "openai_responses"
                    else "deepseek"
                )
            }
        )
    )
    thinking_efforts, thinking_effort_default = _thinking_effort_config(
        protocol, service.slug, capabilities, options, fallback.effort
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
        context_window_tokens=(
            int(options["context_window_tokens"])
            if options.get("context_window_tokens")
            else None
        ),
        # ⚠️ 兜底取 `fallback`（旧全局配置）而**不是** `capabilities` ——
        # 能力是「会不会思考」，档案没写偏好时该跟随用户的全局默认。
        # 拿能力当偏好用的后果：设置页里关掉的思考会自己变回开着。
        thinking_default=bool(options.get("thinking", fallback.thinking_default)),
        effort=thinking_effort_default or "",
        thinking_efforts=thinking_efforts,
    )


def _default_profile_id(settings: Settings, purpose: TargetPurpose) -> int | None:
    return {
        "chat": settings.chat_model_profile_id,
        "consolidation": settings.consolidate_model_profile_id,
        "title": settings.title_model_profile_id,
        "vision": settings.vision_model_profile_id,
    }[purpose]


async def resolve_title_target(
    session: AsyncSession, settings: Settings | None = None
) -> ModelTarget | None:
    """解析用户在模型目录里指定的标题模型；未指定时保留旧标题链路。"""
    settings = settings or await resolve_settings(session)
    if settings.title_model_profile_id is None:
        return None
    try:
        return await resolve_model_target(
            session,
            settings,
            profile_id=settings.title_model_profile_id,
            purpose="title",
        )
    except ValueError as exc:
        logger.warning("标题模型档案不可用：%s", exc)
        return None


async def resolve_vision_target(
    session: AsyncSession, settings: Settings | None = None
) -> ModelTarget | None:
    """解析「看图专用」的模型目标。没配就是 None。

    和另外两个用途不同，这里**没有兜底**：聊天模型看不了图正是要用它的原因，
    退回去只会把图静默丢掉。调用方拿到 None 要给出「去设置页配一个」的明确提示。

    配了但停用/凭据缺失时同样返回 None 并记一条日志 —— 一次带图的对话不该
    因为一个可选功能没配好而整轮失败。
    """
    settings = settings or await resolve_settings(session)
    if settings.vision_model_profile_id is None:
        return None
    try:
        return await resolve_model_target(session, settings, purpose="vision")
    except ValueError as exc:
        logger.warning("视觉模型档案不可用：%s", exc)
        return None


async def resolve_model_target(
    session: AsyncSession,
    settings: Settings | None = None,
    *,
    profile_id: int | None = None,
    purpose: TargetPurpose = "chat",
    legacy_model_id: str = "",
) -> ModelTarget:
    """解析会话/全局/旧配置，返回一个可执行的模型目标。

    不传 `settings` 时从**数据库**解析生效配置，而不是退回 `.env` 启动快照 ——
    默认模型正是存在 `app_settings` 里的，用快照会看不见它。

    ⚠️ `purpose="vision"` 没配档案时**不会**退回聊天模型 —— 那正是它存在的场景
    （聊天模型看不了图）。用 `resolve_vision_target` 拿一个可能为 None 的结果。
    """
    settings = settings or await resolve_settings(session)
    await ensure_builtin_catalog(session, settings)

    if profile_id is None:
        profile_id = _default_profile_id(settings, purpose)

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
    session: AsyncSession,
    settings: Settings | None = None,
    purpose: Literal["chat", "consolidation", "title"] = "chat",
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
    elif purpose == "title":
        default_id = settings.title_model_profile_id
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
        options = profile.options or {}
        fallback = ModelTarget.from_settings(
            settings.model_copy(
                update={
                    "provider": (
                        "anthropic"
                        if service.protocol == "anthropic"
                        else "openai"
                        if service.protocol == "openai_responses"
                        else "deepseek"
                    )
                }
            )
        )
        thinking_efforts, thinking_effort_default = _thinking_effort_config(
            service.protocol, service.slug, capabilities, options, fallback.effort
        )
        reason = ""
        available = bool(service.enabled and profile.enabled)
        if not service.enabled:
            reason = "模型服务已停用"
        elif not profile.enabled:
            reason = "模型已停用"
        elif service.credential_ref and not _secret(service.credential_ref, settings):
            available = False
            reason = f"未配置 {service.credential_ref}"
        elif purpose in {"chat", "consolidation"} and not capabilities.get("tool_calling", False):
            available = False
            reason = "聊天需要支持工具调用"
        elif purpose == "title" and not capabilities.get("text_generation", True):
            available = False
            reason = "标题生成需要文本模型"
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
                "thinking_default": bool(
                    options.get("thinking", fallback.thinking_default)
                ),
                "thinking_efforts": list(thinking_efforts),
                "thinking_effort_default": thinking_effort_default,
                "context_window_tokens": (
                    int(profile.options["context_window_tokens"])
                    if (profile.options or {}).get("context_window_tokens")
                    else None
                ),
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
