"""把一条时间事项写成人话。

日历给你「15:00 产品评审」。这里想给的是「十五分钟后和产品团队开会，你上周说想提
重复检测那件事」—— 差别不在通道，在于这个助手手上有对话上下文，而日历没有。

正文走标题那条便宜链路（`app/llm/title.py`），一条提醒几十个 token。拿不到模型、
模型返回空、**或者太慢**，都退回模板 —— **提醒必须准时发出去**，文案好不好是第二位的。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ConversationSummary, TimelineItem
from app.llm.title import get_title_client
from app.obs import bind
from app.timeutils import aware

logger = logging.getLogger(__name__)

KIND_EMOJI = {
    "event": "📅",
    "todo": "☑️",
    "reminder": "⏰",
    "birthday": "🎂",
    "travel": "✈️",
    "deadline": "⚠️",
    "note": "📝",
}

COPY_SYSTEM = """你在给主人的手机写一条推送通知的正文。

- 一句话，最多 40 个字，不要换行
- 不要重复标题里已经有的事项名称和时间
- 有背景信息就带一句有用的（要准备什么、上次聊到哪），没有就写还剩多久
- 直接说事，不要「温馨提示」「请注意」这类套话，也不要加表情符号
- 只输出这句话本身"""


def kind_emoji(kind: str) -> str:
    return KIND_EMOJI.get(kind, "📌")


def humanize_gap(starts_at: dt.datetime, now: dt.datetime) -> str:
    """还有多久。给正文兜底用，也给模型当输入。"""
    minutes = round((aware(starts_at) - aware(now)).total_seconds() / 60)
    if minutes < -60:
        return f"已经过去 {round(-minutes / 60)} 小时"
    if minutes < 0:
        return f"已经过去 {-minutes} 分钟"
    if minutes == 0:
        return "现在开始"
    if minutes < 60:
        return f"还有 {minutes} 分钟"
    hours = round(minutes / 60)
    if hours < 24:
        return f"还有 {hours} 小时"
    return f"还有 {round(hours / 24)} 天"


def format_clock(item: TimelineItem, all_day_label: str = "全天") -> str:
    if item.all_day:
        return all_day_label
    start = item.starts_at.astimezone()
    if item.ends_at is None:
        return f"{start:%H:%M}"
    return f"{start:%H:%M}–{item.ends_at.astimezone():%H:%M}"


def subtitle_for(item: TimelineItem) -> str:
    """时间和地点。放副标题而不是正文 —— 正文那行留给「为什么现在告诉你」。"""
    parts = [format_clock(item)]
    if item.location:
        parts.append(item.location)
    return " · ".join(parts)


def fallback_body(item: TimelineItem, now: dt.datetime) -> str:
    """模型不可用时的正文。详情优先，没详情就说还剩多久。"""
    details = item.details.strip()
    if details:
        return details[:80]
    return humanize_gap(item.starts_at, now)


async def _recent_recap(session: AsyncSession, conversation_id: int | None) -> str:
    """来源会话最近一次的复盘。这是「模型知道背景」的全部来源。"""
    if conversation_id is None:
        return ""
    row = (
        await session.execute(
            select(ConversationSummary.recap, ConversationSummary.summary)
            .where(ConversationSummary.conversation_id == conversation_id)
            .order_by(ConversationSummary.id.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return ""
    return ((row.recap or row.summary) or "").strip()[:400]


async def compose_body(
    session: AsyncSession, item: TimelineItem, settings: Settings, now: dt.datetime
) -> str:
    """写正文。任何一步出问题都退回模板，绝不让文案挡住送达。"""
    if not settings.notify_smart_copy:
        return fallback_body(item, now)

    client = get_title_client(settings)
    if client is None:
        return fallback_body(item, now)

    background = await _recent_recap(session, item.source_conversation_id)
    prompt = "\n".join(
        part
        for part in (
            f"事项：{item.title}",
            f"类型：{item.kind}",
            f"时间：{format_clock(item)}（{humanize_gap(item.starts_at, now)}）",
            f"地点：{item.location}" if item.location else "",
            f"备注：{item.details.strip()}" if item.details.strip() else "",
            f"这件事来自这轮对话：{background}" if background else "",
        )
        if part
    )

    try:
        # **必须带超时。** 只 try/except 是不够的：AsyncOpenAI 默认等 600 秒，
        # 而 ticker 是单条循环 —— 一次卡住的文案调用会把之后所有提醒一起拖死，
        # 表现为「到点了什么都没响，日志里也没有报错」。实测这条免费链路
        # 偶尔要 45 秒以上，慢比抛异常更危险，因为它不留痕迹。
        with bind(
            session_id=item.source_conversation_id,
            purpose="notify_copy",
        ):
            text = await asyncio.wait_for(
                client.complete(system=COPY_SYSTEM, prompt=prompt, max_tokens=120),
                timeout=settings.notify_timeout,
            )
    except Exception:  # noqa: BLE001 - 外部服务，什么都可能抛；文案不值得让提醒失败
        logger.warning("提醒文案生成失败或超时，退回模板", exc_info=True)
        return fallback_body(item, now)

    # 模型偶尔会加引号或者干脆返回空。收拾一下，收拾不出来就用模板。
    cleaned = text.strip().strip("“”\"'").replace("\n", " ").strip()
    return cleaned[:120] if cleaned else fallback_body(item, now)
