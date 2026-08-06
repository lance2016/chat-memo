"""把请求快照打进日志。

日志里放**轮廓**而不是完整 JSON：一轮对话的 payload 动辄几十 KB，
整段吐到终端就没法看了，而 90% 的问题（历史串了、system 变了、
thinking 块没滤掉、工具定义没带上）看轮廓就能定位。
要逐字节对照时去 ``GET /api/debug/requests/{id}``。
"""

from __future__ import annotations

import logging

from app.debug.recorder import RequestSnapshot, outline
from app.logging_setup import dim


def log_request(logger: logging.Logger, snapshot: RequestSnapshot) -> None:
    head = snapshot.summary()
    conv = f"conv#{head['conversation_id']}" if head["conversation_id"] else "-"
    logger.info(
        "🔍 %s 请求#%s %s",
        conv,
        snapshot.id,
        dim(
            f"{head['provider']}/{head['model']} · 第 {head['iteration'] + 1} 次 · "
            f"system {head['system_chars']} 字 · {head['messages']} 条消息 · "
            f"{head['tools']} 工具"
        ),
    )
    for line in outline(snapshot.payload):
        logger.info("   %s", dim(line))
