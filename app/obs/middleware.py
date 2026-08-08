"""ASGI middleware for request traces and duration/status attributes."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.obs.context import set_current_span_attributes, trace

logger = logging.getLogger(__name__)


class ObservabilityMiddleware:
    """Keep the HTTP span open for the whole streaming response."""

    def __init__(
        self,
        app: Callable[..., Awaitable[Any]],
        access: bool = True,
        trace_reads: bool = False,
        trace_http_paths: str = "/api/chat,/api/jobs/consolidate",
    ) -> None:
        self.app = app
        self.access = access
        self.trace_reads = trace_reads
        self.trace_http_paths = frozenset(
            path.strip().rstrip("/")
            for path in trace_http_paths.split(",")
            if path.strip()
        )

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        path = scope.get("path", "")
        status_code = 500
        started = time.monotonic()

        async def send_with_status(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        trace_request = path.rstrip("/") in self.trace_http_paths or (
            self.trace_reads and method == "GET"
        )
        if path == "/health":
            trace_request = False

        if trace_request:
            with trace("http", f"{method} {path}", method=method, path=path):
                try:
                    await self.app(scope, receive, send_with_status)
                finally:
                    self._finish_request(method, path, status_code, started)
        else:
            try:
                await self.app(scope, receive, send_with_status)
            finally:
                self._finish_request(method, path, status_code, started)

    def _finish_request(
        self, method: str, path: str, status_code: int, started: float
    ) -> None:
        duration_ms = round((time.monotonic() - started) * 1000, 1)
        set_current_span_attributes(
            **{
                "http.method": method,
                "http.route": path,
                "http.status_code": status_code,
                "http.duration_ms": duration_ms,
            }
        )
        if self.access:
            logger.info(
                "%s %s status=%s duration=%.1fms",
                method,
                path,
                status_code,
                duration_ms,
            )
