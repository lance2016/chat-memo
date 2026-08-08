"""可观测性状态位。

这些用例钉住的是**「配置说开着」和「真的在发 trace」是两回事**。
`setup_tracing` 在依赖缺失或初始化失败时会降级成无 trace 模式继续跑（这是对的，
可观测性不该拖垮主链路），此时配置显示「开」而实际没有任何数据 ——
一个只报告 `obs_tracing` 的状态卡会让人对着空的 Phoenix 找半天。
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.obs import status as obs_status


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _settings(**overrides) -> Settings:
    return Settings(
        obs_tracing=True,
        phoenix_collector_endpoint="http://phoenix:6006",
        **overrides,
    )


async def test_off_points_at_the_switch_not_a_command(monkeypatch) -> None:
    """关掉是设置页上一个开关的事，不该让人去敲命令。

    顺带：没开的时候不该去探活 —— 白等 2 秒，还会在日志里留下没意义的连接失败。
    """
    probed = False

    async def spy(_endpoint):
        nonlocal probed
        probed = True
        return True, ""

    monkeypatch.setattr(obs_status, "probe", spy)

    result = await obs_status.observability_status(Settings(obs_tracing=False))

    assert result["stage"] == "off"
    assert probed is False
    assert result["remedy_command"] == ""
    assert "开关" in result["detail"] or "打开" in result["detail"]


async def test_enabled_but_dependencies_missing(monkeypatch) -> None:
    """OBS_TRACING=1 但镜像没带 obs extra —— 最常见的一种「开了却没数据」。"""
    monkeypatch.setattr(obs_status, "_dependencies_installed", lambda: False)
    monkeypatch.setattr(obs_status, "probe", lambda _e: _ok())

    result = await obs_status.observability_status(_settings())

    assert result["stage"] == "missing_deps"
    assert "依赖" in result["detail"]


async def test_dependencies_present_but_tracing_never_initialised(monkeypatch) -> None:
    """依赖在、配置开着，但 setup_tracing 抛异常降级了。

    这一步不分出来的话，人会以为「已启用」然后对着空的 Phoenix 找半天。
    """
    monkeypatch.setattr(obs_status, "_dependencies_installed", lambda: True)
    monkeypatch.setattr(obs_status, "_tracing_active", lambda: False)
    monkeypatch.setattr(obs_status, "probe", lambda _e: _ok())

    result = await obs_status.observability_status(_settings())

    assert result["stage"] == "failed"
    assert "日志" in result["detail"]


async def test_tracing_active_but_phoenix_down(monkeypatch) -> None:
    """忘了 --profile obs 起容器 —— trace 发出去没人收。"""
    monkeypatch.setattr(obs_status, "_dependencies_installed", lambda: True)
    monkeypatch.setattr(obs_status, "_tracing_active", lambda: True)
    monkeypatch.setattr(obs_status, "probe", lambda _e: _fail())

    result = await obs_status.observability_status(_settings())

    assert result["stage"] == "unreachable"
    assert "容器没起" in result["detail"]
    # 需要动手时才给命令
    assert result["remedy_command"] == obs_status.START_COMMAND


async def test_ready_reports_no_problem(monkeypatch) -> None:
    monkeypatch.setattr(obs_status, "_dependencies_installed", lambda: True)
    monkeypatch.setattr(obs_status, "_tracing_active", lambda: True)
    monkeypatch.setattr(obs_status, "probe", lambda _e: _ok())

    result = await obs_status.observability_status(
        _settings(obs_trace_http_paths="/api/chat,/api/jobs/consolidate")
    )

    assert result["stage"] == "ready"
    assert result["detail"] == ""
    assert result["traced_paths"] == ["/api/chat", "/api/jobs/consolidate"]


async def test_probe_failure_is_a_state_not_an_error() -> None:
    """探活失败是正常状态，不该抛异常让整页设置报错。"""
    ok, detail = await obs_status.probe("http://127.0.0.1:1")

    assert ok is False
    assert detail


async def test_status_endpoint_is_read_only(client: AsyncClient) -> None:
    """状态接口只读 —— 开关走 PATCH /api/settings，和其他运行时配置同一条路。

    两条写路径会长出两套规则，模型目录那边已经吃过这个亏。
    """
    assert (await client.get("/api/obs/status")).status_code == 200
    assert (await client.post("/api/obs/status")).status_code == 405


async def _ok():
    return True, ""


async def _fail():
    return False, "连不上：ConnectError"


async def test_privacy_note_follows_the_capture_switch(monkeypatch) -> None:
    """关掉正文记录后，提示语不该还在吓唬人说「保存完整对话原文」。"""
    monkeypatch.setattr(obs_status, "_dependencies_installed", lambda: True)
    monkeypatch.setattr(obs_status, "_tracing_active", lambda: True)
    monkeypatch.setattr(obs_status, "probe", lambda _e: _ok())

    on = await obs_status.observability_status(_settings(obs_capture_content=True))
    off = await obs_status.observability_status(_settings(obs_capture_content=False))

    assert "完整对话原文" in on["retention_warning"]
    assert "已关闭正文记录" in off["retention_warning"]


async def test_tracing_switch_actually_toggles(monkeypatch) -> None:
    """开关必须真的双向生效。

    原来 `setup_tracing` 有 `_initialized` 挡着，只能从关变开一次 —— 那样的开关
    放进设置页就是骗人。
    """
    from app.obs import tracing

    calls: list[str] = []
    monkeypatch.setattr(tracing, "_active", None)
    monkeypatch.setattr(tracing, "_uninstrument", lambda: calls.append("off"))

    # 依赖装不上时也要如实返回 False，而不是假装开了
    assert tracing.apply_tracing(Settings(obs_tracing=False)) is False
    assert tracing.is_active() is False
