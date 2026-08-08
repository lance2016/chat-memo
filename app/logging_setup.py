"""日志实现的兼容入口。

实现挪到了 `app/obs/logging.py`（和其余可观测性代码放一起），这里保留旧的
导入路径。**显式列出转发的名字**而不是 `import *`：星号导入让静态检查看不见
`PrettyFormatter` 从哪来（ruff F405），而一个常年报错的 lint 会让人习惯性忽略
整个 lint 输出 —— 那才是真正的代价。
"""

from app.obs.logging import (
    DropHealthChecks,
    JsonFormatter,
    PrettyFormatter,
    TraceFilter,
    colorize,
    dim,
    ok_mark,
    setup_logging,
    strip_ansi,
)

# 旧名字。改名之前的代码和测试还在用。
ColorFormatter = PrettyFormatter

__all__ = [
    "ColorFormatter",
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
