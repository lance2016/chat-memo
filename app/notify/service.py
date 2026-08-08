"""送达一条通知，并保证不重复。

幂等和重试是同一件事的两面，所以都在这里：

- ticker 是**补跑式**的（查「该发而没发的」而不是精确定时触发），
  进程重启、笔记本睡醒之后一定会重新扫到同一批 —— 靠 dedupe_key 挡住重复。
- 但通道临时抽风不能等于「这条提醒没了」—— 所以没送成的记录留着可重试，
  到 ``MAX_ATTEMPTS`` 才放弃。只认 dedupe_key 会把两者混为一谈。
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Notification
from app.notify.channels import Channel, ChannelError, build_channels
from app.notify.message import PushMessage
from app.obs import trace
from app.obs.context import set_current_span_attributes

logger = logging.getLogger(__name__)

# 三次覆盖「对面重启」这一类抖动。再多就不是抖动，是配置错了，
# 每分钟重试只会把日志刷满而不会成功。
MAX_ATTEMPTS = 3


class Notifier:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        channels: list[Channel] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.channels = build_channels(settings) if channels is None else channels

    @property
    def ready(self) -> bool:
        return bool(self.channels)

    async def deliver(self, message: PushMessage) -> Notification | None:
        with trace(
            "job",
            "notify.deliver",
            purpose="notify",
            notification_kind=message.kind,
            timeline_item_id=message.timeline_item_id,
            dedupe_key=message.dedupe_key,
        ):
            return await self._deliver(message)

    async def _deliver(self, message: PushMessage) -> Notification | None:
        """送出并落记录。已经发过、或已经放弃重试的返回 None。

        一个通道都没配好时直接不做事 —— 否则会把 dedupe_key 占掉，
        等用户配好 Bark，这条提醒已经「发过」了。
        """
        if not self.channels:
            logger.debug("没有可用的通知通道，跳过 %s", message.dedupe_key)
            set_current_span_attributes(
                **{"notify.skipped": True, "notify.reason": "no_channel"}
            )
            return None

        record = await self._claim(message)
        if record is None:
            set_current_span_attributes(
                **{"notify.skipped": True, "notify.reason": "deduplicated"}
            )
            return None

        delivered: list[str] = []
        failures: list[str] = []
        for channel in self.channels:
            try:
                await channel.send(message)
                delivered.append(channel.name)
            except ChannelError as exc:
                failures.append(f"{channel.name}: {exc}")
            except Exception as exc:
                logger.exception("通知通道 %s 抛出未预期的异常", channel.name)
                failures.append(f"{channel.name}: {exc}")

        record.channels = ",".join(delivered)
        record.error = "; ".join(failures)
        if delivered:
            record.delivered_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

        if delivered:
            logger.info("通知已送达 %s → %s", message.dedupe_key, record.channels)
        else:
            logger.warning(
                "通知送达失败 %s（第 %d/%d 次）：%s",
                message.dedupe_key,
                record.attempts,
                MAX_ATTEMPTS,
                record.error,
            )
        set_current_span_attributes(
            **{
                "notify.delivered": bool(delivered),
                "notify.channels": record.channels,
                "notify.attempts": record.attempts,
                "notify.error": record.error,
                **(
                    {"notify.title": message.title}
                    if self.settings.obs_capture_content
                    else {}
                ),
            }
        )
        return record

    async def _claim(self, message: PushMessage) -> Notification | None:
        """抢占 dedupe_key。已送达或已放弃时返回 None，可重试时返回旧记录。"""
        existing = (
            await self.session.execute(
                select(Notification).where(Notification.dedupe_key == message.dedupe_key)
            )
        ).scalar_one_or_none()

        if existing is not None:
            if existing.delivered_at is not None or existing.attempts >= MAX_ATTEMPTS:
                return None
            existing.attempts += 1
            # 重试时刷新文案：上次失败之后事项可能已经改过标题或时间。
            existing.title = message.title
            existing.body = message.body
            existing.url = message.url
            await self.session.commit()
            return existing

        record = Notification(
            dedupe_key=message.dedupe_key,
            kind=message.kind,
            title=message.title,
            body=message.body,
            url=message.url,
            timeline_item_id=message.timeline_item_id,
            attempts=1,
        )
        self.session.add(record)
        try:
            await self.session.commit()
        except IntegrityError:
            # 并发下另一个 tick 刚抢到同一个 key。让给它。
            await self.session.rollback()
            return None
        return record
