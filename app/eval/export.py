"""从真实数据导出样本骨架。

手写样本能覆盖你**想得到**的失败模式；真实数据才带得出你想不到的那些 ——
对话有多啰嗦、话题怎么跳、记忆攒到后来长什么样，编不出来。所以数据集的主体应该
是导出来的，手写样本只用来补充故意构造的边界情况。

导出只做机械的部分：把这天的对话和**当时的**记忆状态冻结下来，`expect` 留空。
期望值必须人来标 —— 让模型标期望再拿去评模型，等于让它自己给自己出考卷。

**记忆快照按时间回溯重建**：直接拿今天的记忆当 `memory_before` 是错的，那是这天
被整理**之后**（还叠加了后来所有天）的状态，拿它当起点等于让模型重做一遍已经做完
的事。`memory_versions` 存了每次变更的不可变快照，正好能还原到任意时刻。
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, Memory, MemoryVersion, Message, live_message
from app.eval.dataset import (
    EvalCase,
    EvalConversation,
    EvalMessage,
    Expectation,
)
from app.timeutils import local_day_bounds

logger = logging.getLogger(__name__)


async def export_day(session: AsyncSession, day: dt.date) -> EvalCase:
    """把某一天导成一条待标注的样本。"""
    start, _ = local_day_bounds(day)
    conversations = await _conversations_on(session, day)
    if not conversations:
        raise ValueError(f"{day.isoformat()} 没有任何对话，导不出样本")

    return EvalCase(
        id=day.isoformat(),
        date=day.isoformat(),
        memory_before=await snapshot_at(session, start),
        conversations=conversations,
        expect=Expectation(),
        note=(
            f"从真实数据导出于 {dt.date.today().isoformat()}。"
            "expect 待人工标注：facts 写该记住的事实点，corrections 写该改掉的旧记录，"
            "forbidden 写明确不该进记忆的东西；这天如果不该写任何记忆就设 no_op=true。"
        ),
    )


async def snapshot_at(session: AsyncSession, when: dt.datetime) -> dict[str, str]:
    """重建 `when` 时刻的记忆状态：path -> content。

    对每个路径取 `created_at <= when` 的最后一个版本，`deleted` 的排除掉。

    **同一时刻的多条版本按 id 排后者胜**，这不是细节：Postgres 的 `now()` 返回的是
    事务开始时间，一次整理里连写好几个文件会拿到**完全相同**的时间戳。只按时间排序
    的话，同一秒内谁覆盖谁是不确定的，同一份数据两次导出可能得到不同的起点。

    有个前提要说清楚：`memory_versions` 是从这张表建起来那天才有的，比它更早的
    记忆没有版本记录。所以太老的日子导出来的快照会偏空 —— 那样的样本要么别用，
    要么手工补齐 `memory_before`。这里选择**如实返回**而不是偷偷用当前状态补全，
    静默地把一个错误的起点塞进数据集，比导出一个明显偏空的快照危险得多。
    """
    rows = (
        await session.execute(
            select(MemoryVersion)
            .where(MemoryVersion.created_at <= when)
            .order_by(MemoryVersion.created_at, MemoryVersion.id)
        )
    ).scalars()

    snapshot: dict[str, str] = {}
    for row in rows:
        if row.operation == "deleted":
            snapshot.pop(row.path, None)
        else:
            snapshot[row.path] = row.content
    return snapshot


async def current_snapshot(session: AsyncSession) -> dict[str, str]:
    """当前记忆状态。给「版本记录不全，只能拿现状凑合」的场景用，调用方要知情。"""
    rows = (await session.execute(select(Memory))).scalars()
    return {row.path: row.content for row in rows}


async def _conversations_on(
    session: AsyncSession, day: dt.date
) -> list[EvalConversation]:
    """这天有消息的会话，只取当天那些消息。

    刻意和 `consolidate._conversations_on` 保持同一套口径（跨天的会话只截取当天
    那一段），否则导出的样本和真实整理看到的输入不是同一个东西。
    """
    start, end = local_day_bounds(day)
    stmt = (
        select(Conversation)
        .join(Message, Message.conversation_id == Conversation.id)
        .where(Message.created_at >= start, Message.created_at < end, live_message())
        .distinct()
        .order_by(Conversation.id)
    )
    conversations = list((await session.execute(stmt)).scalars())

    exported: list[EvalConversation] = []
    for conversation in conversations:
        messages = list(
            (
                await session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation.id,
                        Message.created_at >= start,
                        Message.created_at < end,
                        live_message(),
                    )
                    .order_by(Message.id)
                )
            ).scalars()
        )
        rendered = [
            EvalMessage(role=message.role, text=text)
            for message in messages
            if (text := _plain_text(message))
        ]
        if rendered:
            exported.append(
                EvalConversation(title=conversation.title, messages=rendered)
            )
    return exported


def _plain_text(message: Message) -> str:
    """只留双方说的话，和摘要那步看到的一致 —— thinking / tool_use 不进样本。"""
    return "\n".join(
        block.get("text", "")
        for block in message.content
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()
