"""Small, optional observability boundary for the application.

The application only depends on the two context helpers in this module. The
Phoenix/OpenTelemetry packages are optional, so importing the app without the
``obs`` extra remains a supported configuration.
"""

from app.obs.context import (
    bind,
    current_purpose,
    current_session_id,
    current_trace_id,
    trace,
)

__all__ = [
    "bind",
    "current_purpose",
    "current_session_id",
    "current_trace_id",
    "trace",
]
