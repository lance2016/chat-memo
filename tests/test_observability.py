"""Observability must be useful when enabled and harmless when it is not."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager

import pytest

from app.config import Settings
from app.obs import middleware as obs_middleware
from app.obs.context import (
    _json_attribute,
    add_current_span_event,
    bind,
    current_purpose,
    current_session_id,
    current_trace_id,
    record_llm_input,
    trace,
)
from app.obs.logging import JsonFormatter, TraceFilter, dim
from app.obs.tracing import setup_tracing


def test_context_is_available_without_phoenix() -> None:
    assert current_trace_id() == ""
    assert current_session_id() == ""
    assert current_purpose() == ""

    with trace("http", "GET /health", session_id=7, purpose="health"):
        assert current_session_id() == "7"
        assert current_purpose() == "health"

    assert current_session_id() == ""
    assert current_purpose() == ""


def test_nested_bind_only_overrides_the_fields_it_receives() -> None:
    with bind(session_id=12, purpose="chat"):
        with bind(purpose="title"):
            assert current_session_id() == "12"
            assert current_purpose() == "title"
        assert current_session_id() == "12"
        assert current_purpose() == "chat"


def test_span_event_keeps_only_scalar_decision_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSpan:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, object]]] = []

        def is_recording(self) -> bool:
            return True

        def add_event(self, name: str, *, attributes: dict[str, object]) -> None:
            self.events.append((name, attributes))

    span = FakeSpan()
    monkeypatch.setattr("app.obs.context._current_span", lambda: span)

    add_current_span_event(
        "notify.tick.skipped",
        reason="disabled",
        count=0,
        detail="开关关闭",
        ignored=["not exported"],
    )

    assert span.events == [
        (
            "notify.tick.skipped",
            {"reason": "disabled", "count": 0, "detail": "开关关闭"},
        )
    ]


def test_llm_payload_replaces_image_bytes_at_the_observability_boundary() -> None:
    encoded = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": encoded,
                        },
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{encoded}"
                        },
                    },
                ],
            }
        ]
    }

    serialized = _json_attribute(payload)

    assert encoded not in serialized
    assert "image data omitted" in serialized
    # Observability must never mutate the payload that is subsequently sent.
    assert payload["messages"][0]["content"][0]["source"]["data"] == encoded


def test_llm_invocation_parameters_include_effective_extra_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSpan:
        def __init__(self) -> None:
            self.attributes: dict[str, object] = {}

        def is_recording(self) -> bool:
            return True

        def set_attribute(self, key: str, value: object) -> None:
            self.attributes[key] = value

    span = FakeSpan()
    monkeypatch.setattr("app.obs.context._current_span", lambda: span)

    record_llm_input(
        {
            "model": "deepseek-v4-flash",
            "max_tokens": 12_800,
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "low",
            "stream": True,
            "extra_body": {"thinking": {"type": "enabled"}},
        }
    )

    invocation = json.loads(str(span.attributes["llm.invocation_parameters"]))
    assert invocation == {
        "model": "deepseek-v4-flash",
        "max_tokens": 12_800,
        "reasoning_effort": "low",
        "stream": True,
        "thinking": {"type": "enabled"},
    }
    assert "messages" not in invocation


def test_json_formatter_is_valid_json_and_strips_ansi() -> None:
    record = logging.LogRecord(
        "app.chat.service", logging.INFO, __file__, 1, "hello %s", ("world",), None
    )
    with bind(session_id=12, purpose="chat"):
        TraceFilter().filter(record)
        line = JsonFormatter().format(record)

    data = json.loads(line)
    assert data["message"] == "hello world"
    assert data["session_id"] == "12"
    assert data["purpose"] == "chat"
    assert "\x1b[" not in JsonFormatter().format(
        logging.LogRecord(
            "app.test", logging.INFO, __file__, 1, dim("colored"), (), None
        )
    )


def test_tracing_is_opt_in() -> None:
    assert not setup_tracing(
        Settings(obs_tracing=False, phoenix_collector_endpoint="http://phoenix:6006")
    )


@pytest.mark.asyncio
async def test_http_trace_noise_defaults_to_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    traced: list[str] = []

    @contextmanager
    def fake_trace(kind: str, name: str, **fields: object):
        traced.append(name)
        yield

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"ok"})

    monkeypatch.setattr(obs_middleware, "trace", fake_trace)
    middleware = obs_middleware.ObservabilityMiddleware(app, access=False)

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        return None

    await middleware({"type": "http", "method": "GET", "path": "/api/conversations"}, receive, send)
    await middleware({"type": "http", "method": "GET", "path": "/health"}, receive, send)
    await middleware({"type": "http", "method": "POST", "path": "/api/tts/stop"}, receive, send)
    await middleware({"type": "http", "method": "POST", "path": "/api/chat"}, receive, send)
    await middleware({"type": "http", "method": "POST", "path": "/api/jobs/consolidate"}, receive, send)

    assert traced == ["POST /api/chat", "POST /api/jobs/consolidate"]
