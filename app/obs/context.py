"""Trace and request context helpers.

OTel is deliberately imported lazily here. Observability is an optional
feature: the normal application path must keep working when Phoenix is not
installed or is temporarily unavailable.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_fields: ContextVar[dict[str, str]] = ContextVar("obs_fields", default={})
_tracer: Any = None

_OPENINFERENCE_KINDS = {
    "agent": "AGENT",
    "chain": "CHAIN",
    "http": "CHAIN",
    "job": "CHAIN",
    "ticker": "CHAIN",
    "tool": "TOOL",
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
