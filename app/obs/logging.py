"""Human-readable and JSON logging with the active trace context."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sys
from typing import Any

from app.obs.context import current_fields, current_trace_id, current_trace_short

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"

GREY = "\033[38;5;245m"
BLUE = "\033[38;5;75m"
CYAN = "\033[38;5;80m"
GREEN = "\033[38;5;114m"
YELLOW = "\033[38;5;179m"
RED = "\033[38;5;203m"

LEVEL_STYLE = {
    logging.DEBUG: (GREY, "DBG"),
    logging.INFO: (CYAN, "INF"),
    logging.WARNING: (YELLOW, "WRN"),
    logging.ERROR: (RED, "ERR"),
    logging.CRITICAL: (BOLD + RED, "CRT"),
}

NOISY = (
    "httpx",
    "httpcore",
    "openai",
    "anthropic",
    "watchfiles",
    "asyncio",
    "aiosqlite",
    "multipart",
)

_COLOR_ENABLED = True
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


class TraceFilter(logging.Filter):
    """Copy current trace fields onto each record before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        fields = current_fields()
        record.trace_id = current_trace_id()
        record.trace_short = current_trace_short()
        record.session_id = fields.get("session_id", "")
        record.purpose = fields.get("purpose", "")
        return True


class PrettyFormatter(logging.Formatter):
    def __init__(self, color: bool) -> None:
        super().__init__(datefmt="%H:%M:%S")
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        level_color, level_name = LEVEL_STYLE.get(record.levelno, (GREY, "LOG"))
        name = record.name.split(".")[1] if record.name.startswith("app.") else record.name
        name = name.split(".")[0][:8]
        stamp = self.formatTime(record, self.datefmt)
        message = record.getMessage()
        trace_short = getattr(record, "trace_short", "")
        trace_code = trace_short or "-"

        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)

        if not self.color:
            return f"{stamp} {level_name} {name:<8} {trace_code:<6} {message}"
        return (
            f"{DIM}{stamp}{RESET} "
            f"{level_color}{level_name}{RESET} "
            f"{BLUE}{name:<8}{RESET} "
            f"{trace_code:<6} "
            f"{message}"
        )


class JsonFormatter(logging.Formatter):
    """One valid, ANSI-free JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        message = strip_ansi(record.getMessage())
        data: dict[str, Any] = {
            "time": dt.datetime.fromtimestamp(
                record.created, tz=dt.datetime.now().astimezone().tzinfo
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": message,
            "trace_id": getattr(record, "trace_id", ""),
            "session_id": getattr(record, "session_id", ""),
            "purpose": getattr(record, "purpose", ""),
            "service.name": "chat-memo-api",
        }
        if record.exc_info:
            data["exception"] = strip_ansi(self.formatException(record.exc_info))
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class DropHealthChecks(logging.Filter):
    """Container health checks are expected and add no diagnostic value."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


def setup_logging(
    level: str = "INFO",
    color: bool | None = None,
    access: bool = True,
    log_format: str = "pretty",
) -> None:
    global _COLOR_ENABLED
    if color is None:
        color = os.environ.get("NO_COLOR") is None
    normalized = log_format.lower().strip()
    if normalized not in {"pretty", "json"}:
        normalized = "pretty"
    _COLOR_ENABLED = bool(color) and normalized == "pretty"

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(TraceFilter())
    handler.setFormatter(
        JsonFormatter() if normalized == "json" else PrettyFormatter(_COLOR_ENABLED)
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.addFilter(DropHealthChecks())
    if not access:
        access_logger.setLevel(logging.WARNING)


def colorize(text: str, color: str) -> str:
    """Color a message fragment only when the configured output is pretty."""

    if not _COLOR_ENABLED:
        return text
    return f"{color}{text}{RESET}"


def dim(text: str) -> str:
    return colorize(text, DIM)


def ok_mark(ok: bool) -> str:
    return colorize("✓", GREEN) if ok else colorize("✗", YELLOW)


__all__ = [
    "BLUE",
    "BOLD",
    "CYAN",
    "DIM",
    "GREEN",
    "GREY",
    "LEVEL_STYLE",
    "RED",
    "RESET",
    "YELLOW",
    "DropHealthChecks",
    "JsonFormatter",
    "PrettyFormatter",
    "TraceFilter",
    "colorize",
    "dim",
    "ok_mark",
    "setup_logging",
    "strip_ansi",
]
