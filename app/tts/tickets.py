"""一次性播放令牌，外加「提前合成」。

**为什么需要令牌**：要让浏览器边下边播，音频必须能直接喂给 `<audio src="...">`，
而那是浏览器自己发的 GET —— 带不了 `X-API-Key` 头，也带不了 POST body。
把待朗读的文本换成一个短命令牌，URL 里只出现令牌，就同时解决了这两件事。

**令牌就是凭证**，所以 `GET /api/tts/stream/{token}.mp3` 不再校验 API key。
安全性靠三条：32 位十六进制随机（`secrets`，不可猜）、**用一次即失效**、
限时过期。泄露一个令牌最多让人听到那一段话，而且只有一次机会。

**为什么令牌要自己去合成**（``prefetch``）：句级流水线下，第二句往后的令牌要等
前一句播完浏览器才会来取。等到那时才开始合成，每句之间都会卡一下 —— 流水线就白做了。
所以除了第一句，领令牌时就在后台把音频做出来存着，浏览器来取时直接给。
合成本身有全局锁串行，这些后台任务会按创建顺序排队，正好就是播放顺序。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field

from app.config import Settings
from app.tts.client import synthesize

logger = logging.getLogger(__name__)

# 排在后面的句子要等前面播完才被取走。一段长回答读几分钟很正常，
# 给足余量 —— 令牌用一次即失效，长一点不会堆积。
TTL_SECONDS = 900
# 单人使用，同时排队的朗读不会多。上限只是防内存泄漏。
# 一条长回答能切出几十句，别设得比它还小，否则会把还没播的句子挤掉。
MAX_PENDING = 128


@dataclass
class Ticket:
    token: str
    text: str
    # 存一份配置快照：领令牌到真正播放之间，用户可能在设置页改了音色。
    # 用当时那份才符合预期 —— 他点播放时看到的是哪个配置，听到的就该是哪个。
    settings: Settings
    created_at: float = field(default_factory=time.monotonic)
    # 提前合成的后台任务。None 表示到播放时才现做（流式，首字节更快）。
    task: asyncio.Task[tuple[bytes, str]] | None = None

    def expired(self, now: float | None = None) -> bool:
        return (now or time.monotonic()) - self.created_at > TTL_SECONDS

    def cancel(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()


class TicketStore:
    def __init__(self) -> None:
        self._items: dict[str, Ticket] = {}

    def issue(self, text: str, settings: Settings, *, prefetch: bool = False) -> Ticket:
        """登记一段待朗读文本。

        ``prefetch=True`` 时立刻在后台开始合成 —— 用于句级流水线里第二句往后。
        第一句不要预取：它要的是尽快出声，走流式比等整段合成完更快。
        """
        self._sweep()
        if len(self._items) >= MAX_PENDING:
            # 挤掉最老的那个。到这个量级只可能是前端在空转，丢弃是对的。
            oldest = min(self._items.values(), key=lambda t: t.created_at)
            self._drop(oldest.token)

        ticket = Ticket(token=secrets.token_hex(16), text=text, settings=settings)
        if prefetch:
            ticket.task = asyncio.create_task(synthesize(settings, text))
            # 任务可能在没人 await 之前就失败（服务挂了），先吃掉异常避免
            # "Task exception was never retrieved"；真正的错误在 redeem 时再抛。
            ticket.task.add_done_callback(_swallow)
        self._items[ticket.token] = ticket
        return ticket

    def redeem(self, token: str) -> Ticket | None:
        """取出并**立即删除**。过期的当作不存在。"""
        self._sweep()
        ticket = self._items.pop(token, None)
        if ticket is None or ticket.expired():
            if ticket is not None:
                ticket.cancel()
            return None
        return ticket

    def cancel_all(self) -> int:
        """丢弃所有还没播的令牌，返回丢了几个。

        用户按停止时调 —— 队列里剩下的句子不但没人听，还占着合成锁拖慢下一次朗读。
        """
        count = len(self._items)
        for token in list(self._items):
            self._drop(token)
        return count

    def _drop(self, token: str) -> None:
        ticket = self._items.pop(token, None)
        if ticket is not None:
            ticket.cancel()

    def _sweep(self) -> None:
        now = time.monotonic()
        for token in [t for t, item in self._items.items() if item.expired(now)]:
            self._drop(token)

    def clear(self) -> None:
        self.cancel_all()

    def __len__(self) -> int:
        return len(self._items)


def _swallow(task: asyncio.Task[tuple[bytes, str]]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("预合成失败，播放时再报给前端：%s", exc)


tickets = TicketStore()
