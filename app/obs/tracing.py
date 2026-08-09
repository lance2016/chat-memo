"""Phoenix / OpenInference 的开关。

**这是一个可以来回拨的开关，不是启动期的一锤子买卖。** 原来 `setup_tracing` 有个
`_initialized` 全局挡着，只能从「关」变「开」一次 —— 于是 `obs_tracing` 只能放在
`.env` 里，用户要开就得改文件重启，而设置页上放个开关会点了没反应。

现在 `apply_tracing()` 是**幂等的状态调和**：把当前状态调成配置要求的样子，
开→关走 instrumentor 的 `uninstrument()`。所以这个开关可以住在设置页，
和其他运行时配置一样改完立刻生效。

隐私开关（是否把完整 prompt/回复正文写进 Phoenix）同理：OpenInference 在
instrument 时读环境变量，所以改它要重新 instrument 一遍 —— 调和函数顺手就做了。
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit, urlunsplit

from app.config import Settings
from app.obs.context import configure_tracer

logger = logging.getLogger(__name__)

# 当前实际生效的样子。None = 没开。存下来是为了判断「配置变了吗」——
# 只有真的变了才重新 instrument，否则每次 PATCH 设置都白折腾一遍。
_active: tuple[str, str, bool] | None = None

CAPTURE_ENV = "OPENINFERENCE_CAPTURE_MESSAGE_CONTENT"
# Keep text and tool content visible in Phoenix while preventing provider
# instrumentors from copying the actual image bytes into message attributes.
HIDE_INPUT_IMAGES_ENV = "OPENINFERENCE_HIDE_INPUT_IMAGES"


def _otlp_http_endpoint(endpoint: str) -> str:
    """Accept the convenient Phoenix base URL used in docker-compose."""

    parsed = urlsplit(endpoint.rstrip("/"))
    if parsed.path in ("", "/"):
        parsed = parsed._replace(path="/v1/traces")
    return urlunsplit(parsed)


def is_active() -> bool:
    """这个进程现在真的在发 trace 吗。

    和 `settings.obs_tracing` 不是一回事：配置说开着、但依赖缺失或初始化失败时，
    这里是 False。状态卡靠它区分「开了」和「开了但没生效」。
    """
    return _active is not None


def _desired(settings: Settings) -> tuple[str, str, bool] | None:
    if not settings.obs_tracing or not settings.phoenix_collector_endpoint.strip():
        return None
    return (
        settings.obs_project_name,
        settings.phoenix_collector_endpoint,
        settings.obs_capture_content,
    )


def _uninstrument() -> None:
    global _active
    try:
        from openinference.instrumentation.anthropic import AnthropicInstrumentor
        from openinference.instrumentation.openai import OpenAIInstrumentor

        AnthropicInstrumentor().uninstrument()
        OpenAIInstrumentor().uninstrument()
    except Exception:
        # 关不干净不该让请求失败 —— 记下来，状态位仍然置空，下次开会重新 instrument
        logger.exception("Phoenix tracing 停用时出错")
    configure_tracer(None)
    _active = None
    logger.info("Phoenix tracing 已停用")


def apply_tracing(settings: Settings) -> bool:
    """把 tracing 调成配置要求的样子。幂等，可反复调用。

    返回调用后是否在发 trace。**任何失败都降级成「不发 trace」而不是抛异常** ——
    可观测性挂了不该拖垮聊天，这条比拿到 trace 重要。
    """
    global _active
    desired = _desired(settings)

    if desired == _active:
        return _active is not None
    if desired is None:
        _uninstrument()
        return False
    if _active is not None:
        # 换项目名、换地址、或改隐私开关都要重来一遍：OpenInference 在
        # instrument 时读环境变量和 provider，之后改都不生效。
        _uninstrument()

    project, endpoint, capture = desired
    try:
        from openinference.instrumentation.anthropic import AnthropicInstrumentor
        from openinference.instrumentation.openai import OpenAIInstrumentor
        from opentelemetry import trace as trace_api
        from phoenix.otel import register
    except ImportError as exc:
        logger.warning("obs 依赖未安装，继续运行但不发送 Phoenix trace: %s", exc)
        return False

    try:
        # 应用自建的 span 和 SDK 埋点的 span 要一致：正文记不记由同一个开关决定。
        # 用 setdefault 会让设置页里关掉之后无法再打开（环境变量已存在），所以直接赋值。
        os.environ[CAPTURE_ENV] = "true" if capture else "false"
        os.environ[HIDE_INPUT_IMAGES_ENV] = "true"
        provider = register(
            project_name=project,
            endpoint=_otlp_http_endpoint(endpoint),
            protocol="http/protobuf",
            batch=True,
        )
        AnthropicInstrumentor().instrument(tracer_provider=provider)
        OpenAIInstrumentor().instrument(tracer_provider=provider)
        configure_tracer(trace_api.get_tracer(project))
        _active = desired
    except Exception:
        logger.exception("Phoenix tracing 初始化失败，已降级为无 trace 模式")
        _active = None
        return False

    logger.info(
        "Phoenix tracing 已启用 project=%s endpoint=%s 记录正文=%s",
        project, endpoint, capture,
    )
    return True


# 旧名字。`main.create_app` 之外还有别处引用时不至于炸。
setup_tracing = apply_tracing
