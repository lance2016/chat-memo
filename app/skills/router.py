"""技能管理接口：列出、安装、启停、删除。

**安装只能由人发起。** 这里没有对应的模型工具，模型不能给自己装技能 ——
技能正文是会被当成指令执行的，让模型自己决定装什么等于把边界交出去。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.security import require_api_key
from app.settings_store import resolve_settings
from app.skills import service
from app.skills.errors import (
    InvalidSkillManifest,
    InvalidSkillPath,
    SkillError,
    SkillInstallError,
    SkillNotFound,
)
from app.skills.install import MAX_DOWNLOAD_BYTES, InstallOutcome
from app.skills.paths import normalize_name

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/skills", tags=["skills"], dependencies=[Depends(require_api_key)]
)


class SkillOut(BaseModel):
    name: str
    description: str
    version: str
    license: str
    allowed_tools: list[str]
    files: list[str]
    size_bytes: int
    enabled: bool
    source: str
    ref: str
    installed_at: Any
    # 非空表示这个技能坏了：仍然列出来，但模型看不到它
    error: str
    # 装得上但值得说一句的问题。不影响可见性
    warning: str


class SkillCatalogOut(BaseModel):
    # 技能目录的实际位置。装不上时第一个要看的就是它
    root: str
    # 总开关（设置页里的 skills_enabled）。关掉时技能仍然列出来，只是不进对话
    enabled: bool
    total: int
    active: int
    skills: list[SkillOut]


class SkillDetailOut(SkillOut):
    body: str


class InstallRequest(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    overwrite: bool = True


class InstalledOut(BaseModel):
    name: str
    description: str
    replaced: bool


class SkippedOut(BaseModel):
    path: str
    reason: str


class InstallResultOut(BaseModel):
    installed: list[InstalledOut]
    # 包里有 SKILL.md 但装不了的。**必须回给前端** —— 合集里跳过了一个而界面
    # 只说「装好了 17 个」，人根本不会发现自己想要的那个恰好没装上
    skipped: list[SkippedOut] = []


class EnableRequest(BaseModel):
    enabled: bool


@router.get("", response_model=SkillCatalogOut)
async def list_skills(session: AsyncSession = Depends(get_session)) -> Any:
    settings = await resolve_settings(session)
    views = await service.load_catalog(session, settings)
    return SkillCatalogOut(
        root=settings.skills_path,
        enabled=settings.skills_enabled and bool(settings.skills_path),
        total=len(views),
        active=sum(view.visible_to_model for view in views),
        skills=[_out(view) for view in views],
    )


@router.post("/install", response_model=InstallResultOut)
async def install_skill(
    payload: InstallRequest, session: AsyncSession = Depends(get_session)
) -> Any:
    settings = await resolve_settings(session)
    async with _as_http_error():
        outcome = await service.install(
            session, settings, payload.source, overwrite=payload.overwrite
        )
    logger.info(
        "↧ 从 %s 安装了 %s", payload.source,
        "、".join(i.name for i in outcome.installed) or "（无）",
    )
    return _install_result(outcome)


@router.post("/upload", response_model=InstallResultOut)
async def upload_skill(
    file: UploadFile = File(...),
    overwrite: bool = True,
    session: AsyncSession = Depends(get_session),
) -> Any:
    settings = await resolve_settings(session)
    payload = await file.read()
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"文件超过 {MAX_DOWNLOAD_BYTES // 1024 // 1024}MB 上限",
        )
    async with _as_http_error():
        outcome = await service.install_upload(
            session,
            settings,
            payload,
            filename=file.filename or "",
            overwrite=overwrite,
        )
    return _install_result(outcome)


@router.get("/{name}", response_model=SkillDetailOut)
async def get_skill(
    name: str, session: AsyncSession = Depends(get_session)
) -> Any:
    settings = await resolve_settings(session)
    async with _as_http_error():
        skill = normalize_name(name)
        views = {view.entry.name: view for view in
                 await service.load_catalog(session, settings)}
        if skill not in views:
            raise SkillNotFound(f"没有装名为 {skill!r} 的技能")
        view = views[skill]
        # 坏掉的技能读不出正文，但详情页仍要能打开 —— 那正是要看错误原因的时候
        body = ""
        if view.entry.ok:
            manifest = await asyncio.to_thread(
                service.get_store(settings).manifest, skill
            )
            body = manifest.body
        return SkillDetailOut(**_out(view).model_dump(), body=body)


@router.patch("/{name}", response_model=SkillOut)
async def set_skill_enabled(
    name: str, payload: EnableRequest, session: AsyncSession = Depends(get_session)
) -> Any:
    settings = await resolve_settings(session)
    async with _as_http_error():
        skill = normalize_name(name)
        await service.set_enabled(session, skill, payload.enabled)
        # 不显式 commit：get_session 在请求正常结束时提交，而 load_catalog 的
        # select 会先 autoflush，所以这里立刻就能读到刚写的启用状态
        for view in await service.load_catalog(session, settings):
            if view.entry.name == skill:
                return _out(view)
        raise SkillNotFound(f"没有装名为 {skill!r} 的技能")


@router.delete("/{name}", status_code=204)
async def delete_skill(
    name: str, session: AsyncSession = Depends(get_session)
) -> None:
    settings = await resolve_settings(session)
    async with _as_http_error():
        await service.uninstall(session, settings, normalize_name(name))


def _install_result(outcome: InstallOutcome) -> InstallResultOut:
    return InstallResultOut(
        installed=[InstalledOut(**vars(item)) for item in outcome.installed],
        skipped=[SkippedOut(**vars(item)) for item in outcome.skipped],
    )


def _out(view: service.SkillView) -> SkillOut:
    entry = view.entry
    return SkillOut(
        name=entry.name,
        description=entry.description,
        version=entry.version,
        license=entry.license,
        allowed_tools=list(entry.allowed_tools),
        files=list(entry.files),
        size_bytes=entry.size_bytes,
        enabled=view.enabled,
        source=view.source,
        ref=view.ref,
        installed_at=view.installed_at,
        error=entry.error,
        warning=entry.warning,
    )


@asynccontextmanager
async def _as_http_error() -> AsyncIterator[None]:
    """技能层异常翻译成 HTTP 状态码。消息原样透出去给人看。"""
    try:
        yield
    except SkillNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (InvalidSkillPath, InvalidSkillManifest) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except SkillInstallError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except SkillError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except OSError as exc:
        # 技能目录没挂上、只读挂载、磁盘满 —— 这些都长成 OSError，
        # 而 500 + 一句 "Internal Server Error" 完全没法排查
        logger.exception("技能目录操作失败")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"技能目录不可用：{exc}"
        ) from exc
