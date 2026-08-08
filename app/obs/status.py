"""可观测性的状态位。

**为什么是状态而不是开关。** Phoenix 这套东西的三个前提全在启动之前就定死了：

1. `INSTALL_OBS=1` 是**镜像构建参数**（Dockerfile），可选依赖得先装进镜像
2. `OBS_TRACING=1` 在 `create_app()` 里被 `setup_tracing` 读一次，之后有 `_initialized` 挡着
3. `phoenix` 容器要跟着 `--profile obs` 起来

所以设置页上放一个「启用 Phoenix」的开关是骗人的：点了没反应，而且看不出为什么。
运行时可改的配置走 `settings_store` 的白名单，这一类只能如实报告现状 + 告诉人怎么开。

同一个道理已经用在别处：知识库是 `kb_enabled` 状态位（vault 是挂载层的事），
语音是 `/api/tts/status` 探活。这里是第三个。
"""

from __future__ import annotations

import logging

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

# 探活给 2 秒。Phoenix 要么在本机 compose 网络里、要么根本没起，
# 等更久只会让设置页卡着转圈。
PROBE_TIMEOUT = 2.0


def _dependencies_installed() -> bool:
    """可选依赖装没装进镜像。装的是 `pyproject` 里的 `obs` extra。"""
    try:
        import phoenix.otel  # noqa: F401
        from openinference.instrumentation.anthropic import (  # noqa: F401
            AnthropicInstrumentor,
        )
    except ImportError:
        return False
    return True


def _tracing_active() -> bool:
    """这个进程真的在发 trace 吗。

    和 `obs_tracing` 配置项不是一回事：配了但依赖没装、或初始化抛异常时，
    `setup_tracing` 会降级成无 trace 模式继续跑（这是对的，可观测性不该拖垮主链路），
    此时配置显示「开」而实际没有数据 —— 那正是最容易浪费时间的状态。
    """
    from app.obs import tracing

    return bool(getattr(tracing, "_initialized", False))


async def probe(endpoint: str) -> tuple[bool, str]:
    """Phoenix 在不在。返回 (可达, 说明)。"""
    if not endpoint.strip():
        return False, "未配置 PHOENIX_COLLECTOR_ENDPOINT"
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            response = await client.get(endpoint.rstrip("/"))
        return response.status_code < 500, ""
    except httpx.HTTPError as exc:
        # 不抛：探活失败是一种正常状态，不该让设置页整页报错
        return False, f"连不上：{type(exc).__name__}"


async def observability_status(settings: Settings) -> dict[str, object]:
    """给设置页的一张状态卡。"""
    installed = _dependencies_installed()
    active = _tracing_active()
    reachable, probe_detail = (
        await probe(settings.phoenix_collector_endpoint) if settings.obs_tracing
        else (False, "")
    )

    # 按「离能用还差哪一步」给一句话，而不是罗列一堆布尔值让人自己拼
    if not settings.obs_tracing:
        stage, detail = "off", "未启用。启用需要重建镜像并重启，见下方命令。"
    elif not installed:
        stage, detail = (
            "missing_deps",
            "已设 OBS_TRACING=1，但镜像里没有 obs 可选依赖 —— 需要带 INSTALL_OBS=1 重建。",
        )
    elif not active:
        stage, detail = (
            "failed",
            "依赖在，但 tracing 没初始化成功。看 api 日志里 setup_tracing 那条。",
        )
    elif not reachable:
        stage, detail = (
            "unreachable",
            f"tracing 已启用，但 Phoenix 连不上{'：' + probe_detail if probe_detail else ''}。"
            "多半是没带 --profile obs 起容器。",
        )
    else:
        stage, detail = "ready", ""

    return {
        "stage": stage,
        "enabled": settings.obs_tracing,
        "installed": installed,
        "active": active,
        "reachable": reachable,
        "project": settings.obs_project_name,
        "collector_endpoint": settings.phoenix_collector_endpoint,
        "traced_paths": [
            path.strip()
            for path in settings.obs_trace_http_paths.split(",")
            if path.strip()
        ],
        "trace_reads": settings.obs_trace_reads,
        "detail": detail,
        # 启用命令写在后端，前端不硬编码 —— 改了构建方式只用改这一处
        "enable_command": (
            "INSTALL_OBS=1 OBS_TRACING=1 docker compose --profile obs up -d --build api phoenix"
        ),
        # ⚠️ Phoenix 里存的是完整 prompt 和回复原文，包括记忆正文
        "retention_warning": "Phoenix 保存完整对话原文，注意 PHOENIX_RETENTION_DAYS 和端口只绑本机",
    }
