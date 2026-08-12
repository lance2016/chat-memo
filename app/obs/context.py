"""Trace and request context helpers.

OTel is deliberately imported lazily here. Observability is an optional
feature: the normal application path must keep working when Phoenix is not
installed or is temporarily unavailable.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any

# 默认值用不可变映射：ContextVar 的 default 是**所有上下文共享的同一个对象**，
# 给它一个可变 dict 意味着任何一次就地修改都会永久污染整个进程的默认值。
# 现在的代码每次都先 `dict(...)` 拷贝再 set，所以安全 —— 但哪天有人写了
# `current_fields()["x"] = 1` 就会静默出事。改成 MappingProxyType 后那种写法会当场抛错。
_EMPTY_FIELDS: Mapping[str, str] = MappingProxyType({})
_fields: ContextVar[Mapping[str, str]] = ContextVar("obs_fields", default=_EMPTY_FIELDS)
_tracer: Any = None

_OPENINFERENCE_KINDS = {
    "agent": "AGENT",
    "chain": "CHAIN",
    "http": "CHAIN",
    "job": "CHAIN",
    "ticker": "CHAIN",
    "tool": "TOOL",
    "llm": "LLM",
}


def configure_tracer(tracer: Any) -> None:
    """Set the application tracer after Phoenix has been registered."""

    global _tracer
    _tracer = tracer


def _current_span() -> Any:
    try:
        from opentelemetry import trace as trace_api
    except ImportError:
        return None
    return trace_api.get_current_span()


def current_trace_id() -> str:
    """Return the current OTel trace ID, or an empty string without tracing."""

    span = _current_span()
    if span is None:
        return ""
    context = span.get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else ""


def current_trace_short() -> str:
    """Return the six-character trace code used in human-readable logs."""

    return current_trace_id()[:6]


def current_session_id() -> str:
    return _fields.get().get("session_id", "")


def current_purpose() -> str:
    return _fields.get().get("purpose", "")


def current_fields() -> dict[str, str]:
    """Return a copy for log formatters; callers cannot mutate the context."""

    return dict(_fields.get())


def _context_helpers() -> tuple[Any, Any]:
    """Load Phoenix's helpers, with compatibility for pre-0.16 Phoenix OTEL."""

    # OBS_TRACING=0 must remain a true no-op even if the optional packages are
    # installed in the environment.
    if _tracer is None:
        return None, None
    try:
        from phoenix.otel import using_metadata, using_session
    except ImportError:
        try:
            from openinference.instrumentation import using_metadata, using_session
        except ImportError:
            return None, None
    return using_session, using_metadata


def _set_span_attributes(attributes: dict[str, Any]) -> None:
    span = _current_span()
    if span is None or not span.is_recording():
        return
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            span.set_attribute(key, value)


def set_current_span_attributes(**attributes: Any) -> None:
    """Set attributes on the active span when tracing is enabled."""

    _set_span_attributes(attributes)


def add_current_span_event(name: str, **attributes: Any) -> None:
    """Add a small, structured decision/event record to the active span.

    Attributes are deliberately limited to scalar values so this stays
    compatible with OpenTelemetry exporters and remains useful in Phoenix's
    Events panel instead of becoming an opaque JSON blob.
    """

    span = _current_span()
    if span is None or not span.is_recording():
        return
    event_attributes = {
        key: value
        for key, value in attributes.items()
        if value is not None and isinstance(value, (str, bool, int, float))
    }
    span.add_event(name, attributes=event_attributes)


def _json_attribute(value: Any) -> str:
    """Serialize an LLM payload for Phoenix's standard input/output fields.

    Image blocks have already been hydrated by the time they reach a provider,
    so serializing the payload directly would put the complete base64 image in
    Phoenix. Keep the useful shape of the request, but replace the binary
    field with a small, human-readable placeholder at this boundary.
    """

    value = _sanitize_media_for_observability(value)
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _image_placeholder(media_type: Any, encoded_length: int) -> str:
    """Return a compact description without retaining image bytes."""

    mime = str(media_type or "image/*")
    # Base64 expands binary data by roughly 4/3. The estimate is only a UI
    # hint; it must never be used as a source of truth for accounting.
    size_kib = max(1, round(encoded_length * 3 / 4 / 1024))
    return f"[image data omitted: {mime}, about {size_kib} KiB]"


def _sanitize_media_for_observability(value: Any) -> Any:
    """Copy a payload while replacing base64 image data with placeholders.

    This handles both native Anthropic image blocks and the OpenAI-compatible
    ``data:image/...;base64,...`` URL shape. It intentionally returns a copy:
    the original object is the request sent to the model and must stay intact.
    """

    if isinstance(value, Mapping):
        copied = {
            key: _sanitize_media_for_observability(item)
            for key, item in value.items()
        }
        source = value.get("source")
        if (
            isinstance(source, Mapping)
            and source.get("type") == "base64"
            and isinstance(source.get("data"), str)
        ):
            sanitized_source = dict(copied.get("source", {}))
            sanitized_source["data"] = _image_placeholder(
                source.get("media_type"), len(source["data"])
            )
            copied["source"] = sanitized_source
        return copied
    if isinstance(value, list):
        return [_sanitize_media_for_observability(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_media_for_observability(item) for item in value)
    if isinstance(value, str) and value.startswith("data:image/"):
        header, _, encoded = value.partition(",")
        if ";base64" in header:
            media_type = header[5:].split(";", 1)[0]
            return _image_placeholder(media_type, len(encoded))
    return value


def _capture_llm_content(*, output: bool = False) -> bool:
    """Honor OpenInference's privacy switches for application-owned spans."""

    false_values = {"0", "false", "no", "off"}
    if (
        os.environ.get("OPENINFERENCE_CAPTURE_MESSAGE_CONTENT", "true").lower()
        in false_values
    ):
        return False
    names = (
        ("OPENINFERENCE_HIDE_OUTPUTS", "OPENINFERENCE_HIDE_OUTPUT_MESSAGES")
        if output
        else ("OPENINFERENCE_HIDE_INPUTS", "OPENINFERENCE_HIDE_INPUT_MESSAGES")
    )
    return not any(
        os.environ.get(name, "").lower() not in {"", *false_values}
        for name in names
    )


def record_llm_input(payload: Any, *, model: str = "") -> None:
    """Attach the exact request payload to the active LLM span.

    SDK auto-instrumentation is useful for token counts, but its message-content
    capture varies by SDK/instrumentation version. The application already has
    the exact payload at the send site, so record it explicitly using the
    OpenInference fields Phoenix renders in the span input panel.
    """

    attributes: dict[str, Any] = {
        "input.mime_type": "application/json",
    }
    if _capture_llm_content():
        attributes["input.value"] = _json_attribute(payload)
    if isinstance(payload, Mapping):
        # SDK auto-instrumentors intentionally summarize only parameters from
        # their public signature.  Vendor extensions passed through
        # ``extra_body`` (for example DeepSeek's ``thinking`` switch) therefore
        # disappear from Phoenix's invocation panel even though the SDK merges
        # them into the final HTTP JSON.  Record the effective wire-level
        # parameters on our application-owned LLM span and omit bulky content
        # fields that already live in ``input.value``.
        invocation = {
            key: value
            for key, value in payload.items()
            if key not in {"messages", "input", "system", "tools"}
        }
        extra_body = invocation.pop("extra_body", None)
        if isinstance(extra_body, Mapping):
            invocation.update(extra_body)
        attributes["llm.invocation_parameters"] = _json_attribute(invocation)
    if model:
        attributes["llm.model_name"] = model
    _set_span_attributes(attributes)


def record_llm_output(
    payload: Any,
    *,
    usage: dict[str, Any] | None = None,
    stop_reason: str = "",
    error: str = "",
) -> None:
    """Attach an LLM response/error and normalized token counts to Phoenix."""

    attributes: dict[str, Any] = {
        "output.mime_type": "application/json",
    }
    if _capture_llm_content(output=True):
        attributes["output.value"] = _json_attribute(payload)
    if stop_reason:
        attributes["llm.response.finish_reasons"] = stop_reason
    if error:
        attributes["error.type"] = "LLMError"
        attributes["error.message"] = error

    # Anthropic and OpenAI-compatible providers use different usage keys.
    usage = usage or {}
    prompt = usage.get("input_tokens", usage.get("prompt_tokens"))
    completion = usage.get("output_tokens", usage.get("completion_tokens"))
    total = usage.get("total_tokens")
    if isinstance(prompt, int):
        attributes["llm.token_count.prompt"] = prompt
    if isinstance(completion, int):
        attributes["llm.token_count.completion"] = completion
    if isinstance(total, int):
        attributes["llm.token_count.total"] = total
    _set_span_attributes(attributes)


@contextmanager
def bind(
    *,
    session_id: str | int | None = None,
    purpose: str | None = None,
    **fields: Any,
) -> Iterator[None]:
    """Bind session/purpose fields to logs and OpenInference child spans."""

    values = dict(_fields.get())
    incoming: dict[str, Any] = {**fields}
    if session_id is not None:
        incoming["session_id"] = session_id
    if purpose is not None:
        incoming["purpose"] = purpose
    values.update(
        {key: str(value) for key, value in incoming.items() if value is not None}
    )
    token = _fields.set(values)

    using_session, using_metadata = _context_helpers()
    try:
        with ExitStack() as stack:
            if session_id is not None and using_session is not None:
                stack.enter_context(using_session(session_id=str(session_id)))
            if purpose is not None and using_metadata is not None:
                stack.enter_context(using_metadata({"purpose": purpose}))

            _set_span_attributes(
                {
                    "app.session_id": values.get("session_id"),
                    "app.purpose": values.get("purpose"),
                    **{
                        f"app.{key}": value
                        for key, value in fields.items()
                        if value is not None
                    },
                }
            )
            yield
    finally:
        _fields.reset(token)


@contextmanager
def trace(kind: str, name: str, **fields: Any) -> Iterator[None]:
    """Create an application span, or a no-op context without the obs extra."""

    attributes = {
        "app.trace_kind": kind,
        "openinference.span.kind": _OPENINFERENCE_KINDS.get(kind, "CHAIN"),
        **{
            f"app.{key}": value
            for key, value in fields.items()
            if value is not None and isinstance(value, (str, bool, int, float))
        },
    }

    if _tracer is None:
        with bind(**fields):
            yield
        return

    with _tracer.start_as_current_span(name, attributes=attributes) as span:
        try:
            with bind(**fields):
                yield
        except BaseException as exc:
            if span.is_recording():
                span.record_exception(exc)
                try:
                    from opentelemetry.trace import Status, StatusCode

                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                except ImportError:
                    pass
            raise
