"""Small, optional observability boundary for the application.

The application only depends on the context and LLM payload helpers in this
module. The Phoenix/OpenTelemetry packages are optional, so importing the app
without the ``obs`` extra remains a supported configuration.
"""

from app.obs.context import (
    add_current_span_event,
    bind,
    current_purpose,
    current_session_id,
    current_trace_id,
    record_llm_input,
    record_llm_output,
    trace,
)

__all__ = [
    "add_current_span_event",
    "bind",
    "current_purpose",
    "current_session_id",
    "current_trace_id",
    "record_llm_input",
    "record_llm_output",
    "trace",
]
