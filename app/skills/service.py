"""技能的编排层：把磁盘扫描（store）和数据库元数据（Skill 行）合成一份视图。

**这是唯一一处知道「一个技能是否对模型可见」的地方。** 判据有两条，缺一不可：
manifest 解析得通过（坏掉的技能不能进 system prompt），以及没有被停用。
router（界面）、agent（装配）、prompt（第 0 层）都从这里取，不各自再判一遍 ——
判据分散的后果是「界面上显示已停用，模型还在用」这种没人看得出来的不一致。
"""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Skill
from app.skills.install import (
    InstallOutcome,
    Source,
    download,
    install_zip,
    parse_source,
)
from app.skills.store import SkillEntry, SkillStore


@dataclass(frozen=True)
class SkillView:
    """一个技能在界面上的完整样子：磁盘内容 + 数据库元数据。"""

    entry: SkillEntry
    enabled: bool
    source: str
    ref: str
    installed_at: dt.datetime | None

    @property
    def visible_to_model(self) -> bool:
        return self.enabled and self.entry.ok


def get_store(settings: Settings) -> SkillStore:
    return SkillStore(settings.skills_path)


async def load_catalog(session: AsyncSession, settings: Settings) -> list[SkillView]:
    """全部技能，包括停用的和解析失败的 —— 界面要能显示并处理它们。"""
    entries = await asyncio.to_thread(get_store(settings).list)
    rows = {row.name: row for row in (await session.execute(select(Skill))).scalars()}
    return [
        SkillView(
            entry=entry,
            # 没有行 = 手动拷进来的技能，默认启用
            enabled=rows[entry.name].enabled if entry.name in rows else True,
            source=rows[entry.name].source if entry.name in rows else "本地",
            ref=rows[entry.name].ref if entry.name in rows else "",
            installed_at=rows[entry.name].installed_at if entry.name in rows else None,
        )
        for entry in entries
    ]


async def active_entries(
    session: AsyncSession, settings: Settings
) -> tuple[SkillEntry, ...]:
    """这轮对话该让模型看见的技能。system prompt 和工具执行都用它。"""
    return tuple(
        view.entry for view in await load_catalog(session, settings)
        if view.visible_to_model
    )


async def set_enabled(session: AsyncSession, name: str, enabled: bool) -> None:
    """停用 / 启用一个技能。磁盘上的目录不动。"""
    row = await session.get(Skill, name)
    if row is None:
        session.add(Skill(name=name, source="本地", enabled=enabled))
    else:
        row.enabled = enabled


async def install(
    session: AsyncSession,
    settings: Settings,
    raw_source: str,
    *,
    overwrite: bool = True,
) -> InstallOutcome:
    """从一个来源装技能，并记下它是从哪来的。"""
    source = parse_source(raw_source)
    payload = await download(source)
    return await _install_payload(
        session, settings, payload, source, overwrite=overwrite
    )


async def install_upload(
    session: AsyncSession,
    settings: Settings,
    payload: bytes,
    *,
    filename: str = "",
    overwrite: bool = True,
) -> InstallOutcome:
    """从上传的 zip 装技能。没有可回溯的来源，只记个文件名。"""
    source = Source(zip_url="", label=f"上传：{filename or 'skill.zip'}")
    return await _install_payload(
        session, settings, payload, source, overwrite=overwrite
    )


async def _install_payload(
    session: AsyncSession,
    settings: Settings,
    payload: bytes,
    source: Source,
    *,
    overwrite: bool,
) -> InstallOutcome:
    outcome = await asyncio.to_thread(
        install_zip,
        payload,
        settings.skills_path,
        subdir=source.subdir,
        overwrite=overwrite,
    )
    for item in outcome.installed:
        row = await session.get(Skill, item.name)
        if row is None:
            # 重装一个之前被停用的技能，enabled 跟着重置回 True ——
            # 「我刚装完它却不工作」比「重装忘了它是关着的」更让人困惑
            session.add(Skill(name=item.name, source=source.label, ref=source.ref))
        else:
            row.source = source.label
            row.ref = source.ref
            row.enabled = True
    return outcome


async def uninstall(session: AsyncSession, settings: Settings, name: str) -> None:
    """删技能：目录和元数据行一起删。

    先删磁盘再删行 —— 反过来的话，磁盘删除失败会留下一个「有目录、没记录」的
    技能，而那种状态默认是启用的，等于删了个寂寞还删不掉。
    """
    await asyncio.to_thread(get_store(settings).remove, name)
    await session.execute(sa_delete(Skill).where(Skill.name == name))
