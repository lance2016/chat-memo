from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Notification
from app.db.session import get_session
from app.notify.channels import KNOWN_CHANNELS, BarkChannel, build_channels
from app.notify.message import PushMessage
from app.notify.service import Notifier
from app.notify.sweep import sweep
from app.security import require_api_key
from app.settings_store import resolve_settings

router = APIRouter(prefix="/api/notify", tags=["notify"], dependencies=[Depends(require_api_key)])


class ChannelStatus(BaseModel):
    name: str
    enabled: bool
    configured: bool
    reason: str


class NotifyStatus(BaseModel):
    enabled: bool
    ready: bool
    channels: list[ChannelStatus]
    recent: list[NotificationOut]


class NotificationOut(BaseModel):
    id: int
    kind: str
    title: str
    body: str
    channels: str
    error: str
    attempts: int
    created_at: dt.datetime
    delivered_at: dt.datetime | None


class TestResult(BaseModel):
    delivered: bool
    channels: str
    error: str


class SweepResult(BaseModel):
    sent: int
    skipped: str = ""


@router.get("/status", response_model=NotifyStatus)
async def notify_status(session: AsyncSession = Depends(get_session)) -> NotifyStatus:
    """每个通道分别报状态。

    汇总成一个 ready 布尔值不够用 —— 「没收到提醒」和「没有提醒」长得一模一样，
    必须能在界面上看出是哪个通道没配好、最近几条推没推出去。
    """
    settings = await resolve_settings(session)
    enabled_names = {
        name.strip() for name in settings.notify_channels.split(",") if name.strip()
    }
    bark = BarkChannel(settings)
    configured = {"bark": bark.configured}

    statuses = [
        ChannelStatus(
            name=name,
            enabled=name in enabled_names,
            configured=configured.get(name, False),
            reason=""
            if configured.get(name, False)
            else "未填写服务器地址或设备 key",
        )
        for name in KNOWN_CHANNELS
    ]

    recent = list(
        (
            await session.execute(
                select(Notification).order_by(Notification.id.desc()).limit(10)
            )
        ).scalars()
    )
    return NotifyStatus(
        enabled=settings.notify_enabled,
        ready=bool(build_channels(settings)),
        channels=statuses,
        recent=[
            NotificationOut(**{f: getattr(row, f) for f in NotificationOut.model_fields})
            for row in recent
        ],
    )


@router.post("/sweep", response_model=SweepResult)
async def sweep_now(session: AsyncSession = Depends(get_session)) -> SweepResult:
    """立刻扫一遍「该提醒而没提醒的」，不等下一次 tick。

    ticker 每 60 秒才跑一次，而开发时（`JOBS_ENABLED=0`）根本不跑 —— 没有这个入口
    就只能改系统时间或者干等。跑的是和 ticker **完全同一个** `sweep()`，
    不是简化版：那样测出来的东西不作数。

    和 `/test` 的区别：`/test` 无视开关直接发一条假消息验证通道通不通，
    这里走真实链路，`notify_enabled` 关着就不发 —— 否则「关掉了还是收到推送」。
    重复调用是安全的，`dedupe_key` 挡住重发。
    """
    settings = await resolve_settings(session)
    if not settings.notify_enabled:
        return SweepResult(sent=0, skipped="主动通知未开启")
    notifier = Notifier(session, settings)
    if not notifier.ready:
        return SweepResult(sent=0, skipped="没有配置好的通知通道")
    return SweepResult(sent=await sweep(session, settings, notifier))


@router.post("/test", response_model=TestResult)
async def send_test(session: AsyncSession = Depends(get_session)) -> TestResult:
    """发一条测试通知。

    不看 notify_enabled —— 正常的配置顺序就是「先测通，再打开开关」。
    dedupe_key 带时间戳，所以可以连点几次。
    """
    settings = await resolve_settings(session)
    notifier = Notifier(session, settings)
    if not notifier.ready:
        return TestResult(
            delivered=False, channels="", error="没有配置好的通知通道"
        )

    now = dt.datetime.now(dt.UTC)
    record = await notifier.deliver(
        PushMessage(
            dedupe_key=f"test:{now:%Y%m%dT%H%M%S}",
            kind="test",
            title="🔔 通知已接通",
            subtitle=f"{now.astimezone():%-m月%-d日 %H:%M}",
            body="到点的时间事项会像这样推到你手机上。",
            url=settings.notify_public_base_url.strip().rstrip("/") or "",
            group="时间线",
            level="active",
        )
    )
    if record is None:
        return TestResult(delivered=False, channels="", error="通知未能创建")
    return TestResult(
        delivered=record.delivered_at is not None,
        channels=record.channels,
        error=record.error,
    )
