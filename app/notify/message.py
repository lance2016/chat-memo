"""一条待送出的通知。通道无关 —— 各通道自己把它翻译成自家的字段。"""

from __future__ import annotations

from dataclasses import dataclass

# 打扰级别，取值沿用 Bark 的写法（其它通道自行映射或忽略）。
# timeSensitive 能穿透 iOS 的专注模式，只留给「马上就要开始」用；
# 每日简报这种可以等的走 active，别把专注模式的额度浪费掉。
LEVELS = frozenset({"passive", "active", "timeSensitive", "critical"})


@dataclass(frozen=True)
class PushMessage:
    """dedupe_key 是幂等的唯一依据，构造时就要定下来，不能在送达时才生成。"""

    dedupe_key: str
    kind: str  # item | briefing | test
    title: str
    body: str
    subtitle: str = ""
    url: str = ""
    group: str = ""
    level: str = "active"
    timeline_item_id: int | None = None
