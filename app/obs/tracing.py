"""Optional Phoenix/OpenInference initialization."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

from app.config import Settings
from app.obs.context import configure_tracer

logger = logging.getLogger(__name__)

_initialized = False


def _otlp_http_endpoint(endpoint: str) -> str:
    """Accept the convenient Phoenix base URL used in docker-compose."""

    parsed = urlsplit(endpoint.rstrip("/"))
    if parsed.path in ("", "/"):
        parsed = parsed._replace(path="/v1/traces")
    return urlunsplit(parsed)


def setup_tracing(settings: Settings) -> bool:
    """Register Phoenix instrumentation without breaking application startup."""

    global _initialized
    if _initialized:
        return True
    if not settings.obs_tracing or not settings.phoenix_collector_endpoint.strip():
        return False

    try:
        from opentelemetry import trace as trace_api
        from openinference.instrumentation.anthropic import AnthropicInstrumentor
        from openinference.instrumentation.openai import OpenAIInstrumentor
        from phoenix.otel import register
    except ImportError as exc:
        logger.warning(
            "OBS_TRACING 已开启但 obs 可选依赖未安装，继续运行但不发送 Phoenix trace: %s",
            exc,
        )
        return False

    try:
        provider = register(
            project_name=settings.obs_project_name,
            endpoint=_otlp_http_endpoint(settings.phoenix_collector_endpoint),
            protocol="http/protobuf",
            batch=True,
        )
        AnthropicInstrumentor().instrument(tracer_provider=provider)
        OpenAIInstrumentor().instrument(tracer_provider=provider)
        configure_tracer(trace_api.get_tracer(settings.obs_project_name))
        _initialized = True
    except Exception:
        logger.exception("Phoenix tracing 初始化失败，已降级为无 trace 模式")
        return False

    logger.info(
        "Phoenix tracing 已启用 project=%s endpoint=%s",
        settings.obs_project_name,
        settings.phoenix_collector_endpoint,
    )
    return True
