"""`JOBS_ENABLED` 开关，以及它带出来的手动触发入口。

开关存在的理由是开发期热重载：ticker 被反复掐掉重来，600s 的整理 tick 永远等不到
第一次触发。关掉之后要能手动跑，所以 `/api/notify/sweep` 和这个开关是一件事的两半，
放同一个文件里测。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import main
from app.config import Settings
from app.db.session import get_session
from app.main import create_app, lifespan
from app.notify import router as notify_router


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把 lifespan 会起的每个后台任务换成一个只记名字的空协程。

    真任务会连数据库、发 HTTP、跑 agent loop —— 这里要验的只是「建了哪几个」。
    """
    names: list[str] = []

    def spy(name: str):
        async def noop() -> None:
            names.append(name)

        return noop

    async def noop_tracing() -> None:
        """lifespan 直接 await 它，不是后台任务，所以不记名字。"""

    monkeypatch.setattr(main, "_sync_tracing", noop_tracing)
    monkeypatch.setattr(main, "_warm_tts", spy("tts"))
    monkeypatch.setattr(main, "run_daily_consolidation", spy("consolidate"))
    monkeypatch.setattr(main, "run_notification_ticker", spy("notify"))
    monkeypatch.setattr(main, "run_backup_ticker", spy("backup"))
    return names


async def run_lifespan(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    async with lifespan(FastAPI()):
        # 任务是 create_task 出来的，让出一次事件循环它们才会真的跑起来
        await asyncio.sleep(0)


async def test_jobs_disabled_starts_no_tickers(
    started: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    await run_lifespan(
        Settings(jobs_enabled=False, tts_warmup=False, consolidate_auto=True),
        monkeypatch,
    )
    assert started == []


async def test_tts_warmup_ignores_the_jobs_switch(
    started: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """预热打的是宿主机的 mlx 服务，一次性且不烧钱 —— 开发期照样该预热。"""
    await run_lifespan(
        Settings(jobs_enabled=False, tts_warmup=True), monkeypatch
    )
    assert started == ["tts"]


async def test_jobs_enabled_starts_all_three_tickers(
    started: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    await run_lifespan(
        Settings(jobs_enabled=True, tts_warmup=False, consolidate_auto=True),
        monkeypatch,
    )
    assert sorted(started) == ["backup", "consolidate", "notify"]


async def test_consolidate_auto_still_gates_its_own_ticker(
    started: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """jobs_enabled 是总闸，各任务自己的开关还在。"""
    await run_lifespan(
        Settings(jobs_enabled=True, tts_warmup=False, consolidate_auto=False),
        monkeypatch,
    )
    assert sorted(started) == ["backup", "notify"]


async def test_jobs_enabled_is_not_settable_from_the_settings_page() -> None:
    """启动期读一次，放进白名单只会给出一个「点了没反应」的开关。"""
    from app.settings_store import ENV_ONLY, WRITABLE_BY_KEY

    assert "jobs_enabled" in ENV_ONLY
    assert "jobs_enabled" not in WRITABLE_BY_KEY


# ---------- 手动扫描 ----------


def fake_settings(**overrides: Any) -> Settings:
    # 显式给字段，不让 Settings() 去读开发机的 .env
    base = {"notify_enabled": True, "notify_channels": "bark", "bark_key": "k"}
    return Settings(**{**base, **overrides})


async def test_sweep_endpoint_runs_the_same_sweep_as_the_ticker(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fake_sweep(session: Any, settings: Any, notifier: Any) -> int:
        calls.append("swept")
        return 3

    monkeypatch.setattr(notify_router, "resolve_settings", lambda _s: _ready(fake_settings()))
    monkeypatch.setattr(notify_router, "sweep", fake_sweep)

    body = (await client.post("/api/notify/sweep")).json()
    assert body == {"sent": 3, "skipped": ""}
    assert calls == ["swept"]


async def test_sweep_endpoint_respects_the_off_switch(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """关掉通知还能收到推送是最糟的一种 bug，所以这里和 /test 的口径不同。"""
    monkeypatch.setattr(
        notify_router,
        "resolve_settings",
        lambda _s: _ready(fake_settings(notify_enabled=False)),
    )

    body = (await client.post("/api/notify/sweep")).json()
    assert body["sent"] == 0
    assert body["skipped"]


async def test_sweep_endpoint_reports_missing_channel(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        notify_router,
        "resolve_settings",
        lambda _s: _ready(fake_settings(bark_key="")),
    )

    body = (await client.post("/api/notify/sweep")).json()
    assert body["sent"] == 0
    assert "通道" in body["skipped"]


async def _ready(settings: Settings) -> Settings:
    return settings
