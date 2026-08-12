"""可观测性的状态位。

**开关在设置页，这里只回答「现在到底能不能用」。**

`obs_tracing` 和 `obs_capture_content` 是运行时配置（改完立刻生效，见
`app/obs/tracing.apply_tracing`），走 `settings_store` 的白名单，和其他设置一样改。
但「配置说开着」和「真的在发 trace」是两回事：依赖缺失、初始化抛异常、Phoenix
容器没起，都会让前者为真而后者为假 —— 而且**没有任何报错**，因为可观测性挂了
不该拖垮聊天。那正是最浪费时间的状态：对着一个空的 Phoenix 找半天。

所以这张卡的价值全在中间那几个阶段，而不是「开/关」。
"""

from __future__ import annotations

import logging

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

# 探活给 2 秒。Phoenix 要么在本机 compose 网络里、要么根本没起，
# 等更久只会让设置页卡着转圈。
PROBE_TIMEOUT = 2.0

# Phoenix 没起时的补救命令。写在后端，前端不硬编码。
# Phoenix 是默认 Compose 栈的一部分；如果用户手动停掉容器，恢复整套依赖即可。
START_COMMAND = "docker compose up -d"


def _dependencies_installed() -> bool:
    """埋点依赖在不在。

    现在它们是主依赖（不再是 `obs` extra），正常情况恒为真 —— 保留这个判断是因为
    有人可能在没装全的环境里直接跑后端，那时要说清楚是缺依赖，而不是让人去查日志。
    """
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

    走 `tracing.is_active()` 这个公开函数，不要去读模块私有变量：
    这里原来读的是 `_initialized`，`tracing` 内部改名成 `_active` 之后判断就永远
    是 False，而状态卡会一口咬定「初始化失败」—— 一个说谎的状态卡比没有更糟。
    """
    from app.obs.tracing import is_active

    return is_active()


async def probe(endpoint: str) -> tuple[bool, str]:
    """Phoenix 在不在。返回 (可达, 说明)。"""
    if not endpoint.strip():
        return False, "未配置上报地址"
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
        await probe(settings.phoenix_collector_endpoint)
        if settings.obs_tracing
        else (False, "")
    )

    # 按「离能用还差哪一步」给一句话，而不是罗列一堆布尔值让人自己拼
    remedy = ""
    if not settings.obs_tracing:
        stage, detail = "off", "已关闭。用上面的「记录模型调用链路」重新打开，立刻生效。"
    elif not installed:
        stage, detail = (
            "missing_deps",
            "埋点依赖没装上。容器环境重建镜像即可；直接跑后端时执行 uv sync。",
        )
    elif not active:
        stage, detail = (
            "failed",
            "依赖在、开关也开着，但没初始化成功。看 api 日志里 app.obs.tracing 那几条。",
        )
    elif not reachable:
        stage, detail = (
            "unreachable",
            f"链路在记录，但 Phoenix 收不到{'：' + probe_detail if probe_detail else ''}。"
            "多半是它的容器没起。",
        )
        remedy = START_COMMAND
    else:
        stage, detail = "ready", ""

    return {
        "stage": stage,
        "enabled": settings.obs_tracing,
        "capture_content": settings.obs_capture_content,
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
        # 只有需要动手时才给命令；「打开开关」这种事不该让人去敲命令
        "remedy_command": remedy,
        # ⚠️ 记正文时 Phoenix 里存的是完整 prompt 和回复，包括记忆正文
        "retention_warning": (
            "Phoenix 保存完整对话原文（含记忆正文），保留 PHOENIX_RETENTION_DAYS 天，端口只绑本机"
            if settings.obs_capture_content
            else "已关闭正文记录：Phoenix 只保留 token 数、延迟和工具调用链路"
        ),
    }
