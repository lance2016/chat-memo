"""主动通知：提醒时刻、幂等送达、补跑扫描。"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Notification, TimelineItem
from app.notify.channels import BarkChannel, ChannelError, build_channels
from app.notify import compose
from app.notify.compose import fallback_body, humanize_gap, subtitle_for
from app.notify.message import PushMessage
from app.notify.schedule import compute_remind_at, resolve_lead_minutes
from app.notify.service import MAX_ATTEMPTS, Notifier
from app.notify.sweep import (
    briefing_due,
    briefing_text,
    dedupe_key_for,
    due_items,
    sweep_due,
)
from app.timeline.store import TimelineStore

UTC8 = dt.timezone(dt.timedelta(hours=8))


def settings(**overrides: object) -> Settings:
    base = {
        "notify_enabled": True,
        "notify_channels": "bark",
        "bark_key": "test-key",
        "notify_smart_copy": False,
        "notify_default_lead_minutes": 15,
        "notify_all_day_hour": 9,
        "notify_catchup_hours": 6,
    }
    return Settings(**{**base, **overrides})


class FakeChannel:
    """记下收到的消息；``fail`` 为真时模拟通道挂掉。"""

    name = "fake"

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[PushMessage] = []
        self.fail = fail

    @property
    def configured(self) -> bool:
        return True

    async def send(self, message: PushMessage) -> None:
        if self.fail:
            raise ChannelError("通道挂了")
        self.sent.append(message)


# ---------- 提醒时刻 ----------


def test_lead_minutes_falls_back_by_kind_and_keeps_explicit_zero() -> None:
    # 显式 0 是「准点提醒」，不能被当成「没填」而套上默认的 15 分钟。
    assert resolve_lead_minutes("event", 0, 15) == 0
    assert resolve_lead_minutes("event", None, 15) == 15
    assert resolve_lead_minutes("deadline", None, 15) == 3 * 24 * 60
    # note 是纯记录，默认不提醒。
    assert resolve_lead_minutes("note", None, 15) is None
    # 没见过的类型退回全局默认。
    assert resolve_lead_minutes("unknown", None, 20) == 20


def test_remind_at_subtracts_lead_from_start() -> None:
    starts = dt.datetime(2026, 8, 10, 15, 0, tzinfo=UTC8)
    remind = compute_remind_at(
        starts_at=starts, kind="event", status="confirmed", all_day=False,
        timezone="Asia/Shanghai", notify=True, lead_minutes=None,
        default_lead_minutes=15, all_day_hour=9,
    )
    assert remind == dt.datetime(2026, 8, 10, 14, 45, tzinfo=UTC8)


def test_all_day_item_reminds_in_the_morning_not_at_midnight() -> None:
    """全天事项的 starts_at 是当地 00:00，直接减提前量会在半夜响。"""
    birthday = dt.datetime(2026, 8, 10, 0, 0, tzinfo=UTC8)
    remind = compute_remind_at(
        starts_at=birthday, kind="birthday", status="confirmed", all_day=True,
        timezone="Asia/Shanghai", notify=True, lead_minutes=None,
        default_lead_minutes=15, all_day_hour=9,
    )
    # 生日默认提前一天：8/10 的 09:00 往前推 24 小时 = 8/9 09:00。
    assert remind == dt.datetime(2026, 8, 9, 9, 0, tzinfo=UTC8)


@pytest.mark.parametrize(
    "status,notify",
    [("completed", True), ("cancelled", True), ("confirmed", False)],
)
def test_no_remind_at_for_finished_or_muted_items(status: str, notify: bool) -> None:
    assert compute_remind_at(
        starts_at=dt.datetime(2026, 8, 10, 15, 0, tzinfo=UTC8),
        kind="event", status=status, all_day=False, timezone="Asia/Shanghai",
        notify=notify, lead_minutes=None, default_lead_minutes=15, all_day_hour=9,
    ) is None


def test_unknown_timezone_name_does_not_break_all_day_items() -> None:
    """时区名是模型填的，可能是「北京时间」这种非 IANA 写法。"""
    remind = compute_remind_at(
        starts_at=dt.datetime(2026, 8, 10, 0, 0, tzinfo=UTC8),
        kind="todo", status="confirmed", all_day=True, timezone="北京时间",
        notify=True, lead_minutes=0, default_lead_minutes=15, all_day_hour=9,
    )
    assert remind == dt.datetime(2026, 8, 10, 9, 0, tzinfo=UTC8)


# ---------- store 侧的联动 ----------


async def test_store_computes_and_clears_remind_at(session: AsyncSession) -> None:
    store = TimelineStore(session, actor="manual", settings=settings())
    item = await store.create({
        "title": "产品评审",
        "kind": "event",
        "starts_at": dt.datetime(2026, 8, 10, 15, 0, tzinfo=UTC8),
    })
    await session.commit()
    assert item.remind_at == dt.datetime(2026, 8, 10, 14, 45, tzinfo=UTC8)

    # 做完就不该再响 —— 留着旧值会让一件已完成的事继续提醒。
    done = await store.update(item.id, {"status": "completed"})
    assert done.remind_at is None

    reopened = await store.update(item.id, {"status": "confirmed"})
    assert reopened.remind_at is not None


async def test_rescheduling_clears_snooze(session: AsyncSession) -> None:
    store = TimelineStore(session, actor="manual", settings=settings())
    item = await store.create({
        "title": "体检",
        "starts_at": dt.datetime(2026, 8, 10, 9, 0, tzinfo=UTC8),
    })
    await store.snooze(item.id, 30)
    assert item.snoozed_until is not None

    moved = await store.update(
        item.id, {"starts_at": dt.datetime(2026, 8, 12, 9, 0, tzinfo=UTC8)}
    )
    # 改期就是一条新提醒，之前那句「稍后再说」不该继续压着它。
    assert moved.snoozed_until is None


async def test_snooze_rejects_finished_items(session: AsyncSession) -> None:
    store = TimelineStore(session, actor="manual", settings=settings())
    item = await store.create({
        "title": "交报告",
        "status": "completed",
        "starts_at": dt.datetime(2026, 8, 10, 9, 0, tzinfo=UTC8),
    })
    with pytest.raises(Exception, match="不需要推迟"):
        await store.snooze(item.id, 30)


# ---------- 通道 ----------


def test_bark_payload_omits_empty_fields_and_groups_notifications() -> None:
    channel = BarkChannel(settings(bark_sound="", bark_icon=""))
    payload = channel.payload(
        PushMessage(dedupe_key="k", kind="item", title="标题", body="正文")
    )
    assert payload["device_key"] == "test-key"
    assert payload["group"] == "时间线"
    # 空副标题不能发过去，Bark 会照样渲染出一行空白。
    assert "subtitle" not in payload
    assert "sound" not in payload
    assert "url" not in payload


def test_build_channels_skips_unconfigured_bark() -> None:
    assert build_channels(settings(bark_key="")) == []
    assert [c.name for c in build_channels(settings())] == ["bark"]
    # 名字打错时不崩，只是这个通道不工作。
    assert build_channels(settings(notify_channels="typo")) == []


# ---------- 幂等与重试 ----------


async def test_delivery_is_idempotent(session: AsyncSession) -> None:
    """补跑式 ticker 会反复扫到同一批，第二次必须什么都不做。"""
    channel = FakeChannel()
    notifier = Notifier(session, settings(), channels=[channel])
    message = PushMessage(dedupe_key="item:1:20260810T0645", kind="item", title="A", body="B")

    assert await notifier.deliver(message) is not None
    assert await notifier.deliver(message) is None
    assert len(channel.sent) == 1


async def test_failed_delivery_retries_then_gives_up(session: AsyncSession) -> None:
    """通道抽风一分钟不该等于「这条提醒没了」，但也不能永远重试。"""
    broken = FakeChannel(fail=True)
    notifier = Notifier(session, settings(), channels=[broken])
    message = PushMessage(dedupe_key="item:2:20260810T0645", kind="item", title="A", body="B")

    for _ in range(MAX_ATTEMPTS):
        record = await notifier.deliver(message)
        assert record is not None
        assert record.delivered_at is None
    assert await notifier.deliver(message) is None

    stored = (await session.execute(select(Notification))).scalar_one()
    assert stored.attempts == MAX_ATTEMPTS
    assert "通道挂了" in stored.error


async def test_no_channels_does_not_burn_the_dedupe_key(session: AsyncSession) -> None:
    """没配好通道时占掉 key，等用户配好 Bark 这条提醒已经「发过」了。"""
    notifier = Notifier(session, settings(), channels=[])
    message = PushMessage(dedupe_key="item:3:20260810T0645", kind="item", title="A", body="B")
    assert await notifier.deliver(message) is None
    assert (await session.execute(select(Notification))).first() is None

    working = FakeChannel()
    assert await Notifier(session, settings(), channels=[working]).deliver(message) is not None
    assert len(working.sent) == 1


# ---------- 扫描 ----------


async def _item(session: AsyncSession, **values: object) -> TimelineItem:
    store = TimelineStore(session, actor="manual", settings=settings())
    item = await store.create(values)
    await session.commit()
    return item


async def test_due_items_picks_only_what_should_ring_now(session: AsyncSession) -> None:
    now = dt.datetime(2026, 8, 10, 14, 50, tzinfo=dt.UTC)
    soon = await _item(
        session, title="马上开会", kind="event",
        starts_at=now + dt.timedelta(minutes=10),
    )
    await _item(
        session, title="下周的会", kind="event", starts_at=now + dt.timedelta(days=7),
    )
    await _item(
        session, title="已完成", kind="event", status="completed",
        starts_at=now + dt.timedelta(minutes=10),
    )
    await _item(
        session, title="静音了", kind="event", notify=False,
        starts_at=now + dt.timedelta(minutes=10),
    )
    await _item(
        session, title="上周漏掉的", kind="event", starts_at=now - dt.timedelta(days=7),
    )

    due = await due_items(session, now, catchup_hours=6, limit=10)
    assert [item.title for item in due] == [soon.title]


async def test_snoozed_item_stays_quiet_then_rings_again(session: AsyncSession) -> None:
    now = dt.datetime(2026, 8, 10, 14, 50, tzinfo=dt.UTC)
    item = await _item(
        session, title="体检", kind="event", starts_at=now + dt.timedelta(minutes=10)
    )
    store = TimelineStore(session, actor="manual", settings=settings())
    before = dedupe_key_for(item)

    item.snoozed_until = now + dt.timedelta(minutes=20)
    await session.commit()
    assert await due_items(session, now, catchup_hours=6, limit=10) == []

    later = now + dt.timedelta(minutes=25)
    again = await due_items(session, later, catchup_hours=6, limit=10)
    assert [i.title for i in again] == ["体检"]
    # 推迟之后必须是一条新提醒，否则会被之前那次的 dedupe_key 挡掉。
    assert dedupe_key_for(again[0]) != before
    assert store  # 保持 store 引用，避免 linter 误判


async def test_sweep_sends_and_does_not_resend(session: AsyncSession) -> None:
    now = dt.datetime(2026, 8, 10, 14, 50, tzinfo=dt.UTC)
    await _item(
        session, title="产品评审", kind="event", location="线上",
        starts_at=now + dt.timedelta(minutes=10),
    )
    channel = FakeChannel()
    config = settings()
    notifier = Notifier(session, config, channels=[channel])

    assert await sweep_due(session, config, notifier, now) == 1
    assert await sweep_due(session, config, notifier, now) == 0

    sent = channel.sent[0]
    assert "产品评审" in sent.title
    assert "线上" in sent.subtitle
    # 一小时内就要开始的事才配打断专注模式。
    assert sent.level == "timeSensitive"


# ---------- 简报 ----------


def test_briefing_only_fires_inside_its_window() -> None:
    config = settings(notify_briefing=True, notify_briefing_hour=8)
    morning = dt.datetime(2026, 8, 10, 8, 30).astimezone()
    night = dt.datetime(2026, 8, 10, 22, 0).astimezone()
    assert briefing_due(config, morning)
    # 错过窗口就跳过这一天，而不是晚上十点推一条「今天有三件事」。
    assert not briefing_due(config, night)
    assert not briefing_due(settings(notify_briefing=False), morning)


def test_briefing_text_lists_today_and_flags_overdue() -> None:
    today = [
        TimelineItem(title="产品评审", kind="event", all_day=False,
                     starts_at=dt.datetime(2026, 8, 10, 15, 0, tzinfo=UTC8), ends_at=None),
    ]
    overdue = [
        TimelineItem(title="交报销", kind="todo", all_day=False,
                     starts_at=dt.datetime(2026, 8, 7, 10, 0, tzinfo=UTC8), ends_at=None),
    ]
    title, body = briefing_text(today, overdue, [])
    assert title == "☀️ 今天有 1 件事"
    assert "产品评审" in body
    assert "逾期未完成 1 件" in body
    assert "交报销" in body


# ---------- 文案兜底 ----------


def test_fallback_body_prefers_details_then_countdown() -> None:
    now = dt.datetime(2026, 8, 10, 14, 45, tzinfo=UTC8)
    starts = dt.datetime(2026, 8, 10, 15, 0, tzinfo=UTC8)
    with_details = TimelineItem(title="会", details="带上上周的数据", starts_at=starts,
                                all_day=False, ends_at=None)
    assert fallback_body(with_details, now) == "带上上周的数据"

    bare = TimelineItem(title="会", details="", starts_at=starts, all_day=False, ends_at=None)
    assert fallback_body(bare, now) == "还有 15 分钟"


def test_humanize_gap_covers_past_and_future() -> None:
    now = dt.datetime(2026, 8, 10, 12, 0, tzinfo=UTC8)
    assert humanize_gap(now, now) == "现在开始"
    assert humanize_gap(now + dt.timedelta(minutes=30), now) == "还有 30 分钟"
    assert humanize_gap(now + dt.timedelta(hours=5), now) == "还有 5 小时"
    assert humanize_gap(now + dt.timedelta(days=3), now) == "还有 3 天"
    assert humanize_gap(now - dt.timedelta(minutes=20), now) == "已经过去 20 分钟"


async def test_slow_copy_model_falls_back_instead_of_stalling_the_ticker(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文案调用必须有超时。

    只 try/except 不够：AsyncOpenAI 默认等 600 秒，而 ticker 是单条循环 ——
    一次卡住的文案调用会把之后所有提醒一起拖死，而且**不留任何痕迹**
    （到点了什么都没响，日志里也没有报错）。实测这条免费链路会超过 45 秒。
    """
    class HangingClient:
        route = "fake/hanging"

        async def complete(self, **_: object) -> str:
            await asyncio.sleep(60)
            return "永远到不了这里"

    monkeypatch.setattr(compose, "get_title_client", lambda _settings: HangingClient())

    now = dt.datetime(2026, 8, 10, 14, 45, tzinfo=UTC8)
    item = TimelineItem(
        title="会", details="", all_day=False, ends_at=None,
        starts_at=dt.datetime(2026, 8, 10, 15, 0, tzinfo=UTC8),
        source_conversation_id=None,
    )
    config = settings(notify_smart_copy=True, notify_timeout=1)

    started = asyncio.get_running_loop().time()
    body = await compose.compose_body(session, item, config, now)
    elapsed = asyncio.get_running_loop().time() - started

    assert body == "还有 15 分钟"
    assert elapsed < 5, f"文案调用没有被超时切断，耗时 {elapsed:.1f}s"


def test_subtitle_joins_time_and_location() -> None:
    item = TimelineItem(
        title="会", all_day=False, location="三楼会议室",
        starts_at=dt.datetime(2026, 8, 10, 15, 0, tzinfo=UTC8),
        ends_at=dt.datetime(2026, 8, 10, 16, 0, tzinfo=UTC8),
    )
    assert "三楼会议室" in subtitle_for(item)
