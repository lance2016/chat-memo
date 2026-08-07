"""送达通道。

一个通道就是「把 PushMessage 送到某个地方」，失败抛 ChannelError。加通道只需要写一个
带 ``name`` / ``configured`` / ``send`` 的类，再在 ``build_channels`` 里登记一行 ——
调度那一侧完全不需要知道有几个通道、分别是什么。

第一个实现是 Bark（iOS 自建推送，服务端 POST 一个 URL 就完事，手机端不需要开浏览器）。
Web Push 会是第二个：它要 VAPID 密钥和订阅表，但对外仍然只是这个接口。
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from app.config import Settings
from app.notify.message import PushMessage

logger = logging.getLogger(__name__)

# 通道注册表用的名字，也是设置里 notify_channels 的取值。
BARK = "bark"
KNOWN_CHANNELS = (BARK,)


class ChannelError(RuntimeError):
    """送达失败。消息直接进 notifications.error，要能看懂。"""


class Channel(Protocol):
    name: str

    @property
    def configured(self) -> bool: ...

    async def send(self, message: PushMessage) -> None: ...


class BarkChannel:
    """https://bark.day.app —— 走官方服务器或自建服务器都是同一个接口。

    用 JSON body 而不是路径式的 ``/{key}/{title}/{body}``：标题和正文里有斜杠、
    换行或 emoji 时，路径式会被 URL 结构吃掉字符。
    """

    name = BARK

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.server = settings.bark_server.rstrip("/")
        self.key = settings.bark_key.strip()
        self.sound = settings.bark_sound.strip()
        self.icon = settings.bark_icon.strip()
        self.timeout = settings.notify_timeout
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.server and self.key)

    def payload(self, message: PushMessage) -> dict[str, object]:
        """空字段一律不发。Bark 收到空字符串会照样渲染出一行空白副标题。"""
        body: dict[str, object] = {
            "device_key": self.key,
            "title": message.title,
            "body": message.body,
            "level": message.level,
            # 同一个 group 在 iOS 通知中心里会折叠成一叠，不会把锁屏刷满。
            "group": message.group or "时间线",
            # 让通知留在「通知历史」里，划掉之后还能翻回来看。
            "isArchive": "1",
        }
        if message.subtitle:
            body["subtitle"] = message.subtitle
        if message.url:
            body["url"] = message.url
        if self.sound:
            body["sound"] = self.sound
        if self.icon:
            body["icon"] = self.icon
        return body

    async def send(self, message: PushMessage) -> None:
        if not self.configured:
            raise ChannelError("Bark 未配置：缺少服务器地址或设备 key")
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(f"{self.server}/push", json=self.payload(message))
            response.raise_for_status()
            # HTTP 200 不代表送达：key 不对时 Bark 也回 200，靠 body 里的 code 区分。
            data = response.json()
            if int(data.get("code", 200)) != 200:
                raise ChannelError(f"Bark 拒绝了这条通知：{data.get('message') or data}")
        except httpx.HTTPStatusError as exc:
            raise ChannelError(f"Bark 返回 {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ChannelError(f"连不上 Bark 服务器：{exc}") from exc
        except ValueError as exc:
            raise ChannelError("Bark 返回的不是 JSON，检查服务器地址是否正确") from exc
        finally:
            if self._client is None:
                await client.aclose()


def build_channels(settings: Settings) -> list[Channel]:
    """按设置里启用的名单构造通道，跳过没配好的。

    没配好就静默跳过而不是抛错：设置页会单独显示每个通道的状态，
    在这里抛会让整个 ticker 因为一个手滑的配置停摆。
    """
    enabled = {name.strip() for name in settings.notify_channels.split(",") if name.strip()}
    unknown = enabled - set(KNOWN_CHANNELS)
    if unknown:
        logger.warning("忽略未知的通知通道：%s", "、".join(sorted(unknown)))

    channels: list[Channel] = []
    if BARK in enabled:
        bark = BarkChannel(settings)
        if bark.configured:
            channels.append(bark)
    return channels
