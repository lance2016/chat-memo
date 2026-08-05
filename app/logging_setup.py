"""日志配置：彩色、紧凑、只留有用的。

原则是「一轮对话应该在日志里留下一个可读的故事」——谁问了什么、模型调了哪些工具、
花了多少 token、耗时多久。除此之外的都算噪音。
"""

from __future__ import annotations

import logging
import os
import sys

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

# 这些第三方 logger 只在出问题时才有价值，平时压到 WARNING
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


class ColorFormatter(logging.Formatter):
    def __init__(self, color: bool) -> None:
        super().__init__(datefmt="%H:%M:%S")
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        level_color, level_name = LEVEL_STYLE.get(record.levelno, (GREY, "LOG"))
        # app.chat.service → chat；uvicorn.error → uvicorn
        name = record.name.split(".")[1] if record.name.startswith("app.") else record.name
        name = name.split(".")[0][:8]

        stamp = self.formatTime(record, self.datefmt)
        message = record.getMessage()

        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)

        if not self.color:
            return f"{stamp} {level_name} {name:<8} {message}"
        return (
            f"{DIM}{stamp}{RESET} "
            f"{level_color}{level_name}{RESET} "
            f"{BLUE}{name:<8}{RESET} "
            f"{message}"
        )


class DropHealthChecks(logging.Filter):
    """容器健康检查每 15 秒打一条，纯噪音。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


def setup_logging(
    level: str = "INFO", color: bool | None = None, access: bool = True
) -> None:
    if color is None:
        # 容器里 stdout 不是 tty，但 docker logs 能正常渲染 ANSI，所以默认开。
        color = os.environ.get("NO_COLOR") is None

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColorFormatter(color))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    # uvicorn 自带三个 logger，统一交给我们的 handler，避免两套格式混在一起
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.addFilter(DropHealthChecks())
    if not access:
        access_logger.setLevel(logging.WARNING)


def colorize(text: str, color: str) -> str:
    """给日志消息里的关键片段上色。NO_COLOR 时自动退化成纯文本。"""
    if os.environ.get("NO_COLOR") is not None:
        return text
    return f"{color}{text}{RESET}"


def dim(text: str) -> str:
    return colorize(text, DIM)


def ok_mark(ok: bool) -> str:
    return colorize("✓", GREEN) if ok else colorize("✗", YELLOW)
