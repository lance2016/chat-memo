"""从真实数据导出样本。

核心是**时间回溯**：`memory_before` 必须是这天被整理之前的状态。拿当前记忆当起点
是个很容易犯又完全无声的错 —— 那是这天整理完、还叠加了之后所有天的状态，
用它当起点等于让模型重做一遍已经做完的事，评出来的分数虚高且没有意义。
"""

import datetime as dt

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, MemoryVersion, Message
from app.eval.export import export_day, snapshot_at

from app.timeutils import local_day_bounds

DAY = dt.date(2026, 8, 6)


async def seed_day(session: AsyncSession, day: dt.date, *texts: str) -> Conversation:
    when = dt.datetime.combine(day, dt.time(12, 0)).astimezone()
    conversation = Conversation(title="那天的会话", created_at=when, updated_at=when)
    session.add(conversation)
    await session.flush()
    for i, text in enumerate(texts):
        session.add(
            Message(
                conversation_id=conversation.id,
                role="user" if i % 2 == 0 else "assistant",
                content=[{"type": "text", "text": text}],
                created_at=when,
            )
        )
    await session.commit()
    return conversation


async def add_version(
    session: AsyncSession, path: str, content: str, operation: str, when: dt.datetime
) -> None:
    """直接写版本行并指定时间。

    不走 `MemoryStore` 是因为它的 `created_at` 由数据库的 `now()` 填 —— 同一个事务里
    连写几条会拿到完全相同的时间戳，「这一刻之前」就没法测了。
    """
    session.add(
        MemoryVersion(
            path=path,
            content=content,
            operation=operation,
            actor="manual",
            created_at=when,
        )
    )
    await session.commit()


async def test_snapshot_reconstructs_the_state_at_a_point_in_time(
    session: AsyncSession,
) -> None:
    """版本表能还原任意时刻。这是导出能给出正确起点的全部依据。"""
    path = "/memories/profile/preferences.md"
    base = dt.datetime(2026, 8, 1, 10, 0, tzinfo=dt.UTC)
    await add_version(session, path, "- 用 pip 管理依赖", "created", base)
    await add_version(session, path, "- 用 uv 管理依赖", "modified", base + dt.timedelta(days=2))

    old = await snapshot_at(session, base + dt.timedelta(days=1))
    now = await snapshot_at(session, base + dt.timedelta(days=3))

    assert "pip" in old[path]
    assert "uv" in now[path]


async def test_snapshot_breaks_ties_by_id(session: AsyncSession) -> None:
    """同一时刻写的多条版本按 id 排，后者胜。

    Postgres 的 now() 返回事务开始时间，一次整理连写几个文件会拿到相同时间戳 ——
    只按时间排序的话，同一份数据两次导出可能得到不同的起点。
    """
    path = "/memories/a.md"
    same = dt.datetime(2026, 8, 1, 10, 0, tzinfo=dt.UTC)
    await add_version(session, path, "先写的", "created", same)
    await add_version(session, path, "后写的", "modified", same)

    snapshot = await snapshot_at(session, same)

    assert snapshot[path] == "后写的"


async def test_snapshot_respects_deletions(session: AsyncSession) -> None:
    """删掉的记忆不能出现在快照里，否则起点里会多一个当时并不存在的文件。"""
    base = dt.datetime(2026, 8, 1, 10, 0, tzinfo=dt.UTC)
    await add_version(session, "/memories/gone.md", "曾经的内容", "created", base)
    await add_version(
        session, "/memories/gone.md", "曾经的内容", "deleted", base + dt.timedelta(hours=1)
    )

    snapshot = await snapshot_at(session, base + dt.timedelta(days=1))

    assert "/memories/gone.md" not in snapshot


async def test_export_freezes_the_day(session: AsyncSession) -> None:
    await seed_day(session, DAY, "我在做一个聊天项目", "记住了")
    await add_version(
        session,
        "/memories/MEMORY.md",
        "# 记忆索引",
        "created",
        dt.datetime.combine(DAY - dt.timedelta(days=1), dt.time(9, 0), tzinfo=dt.UTC),
    )

    case = await export_day(session, DAY)

    assert case.id == DAY.isoformat() and case.date == DAY.isoformat()
    assert len(case.conversations) == 1
    assert case.conversations[0].messages[0].text == "我在做一个聊天项目"
    assert "/memories/MEMORY.md" in case.memory_before


async def test_export_leaves_expectations_empty(session: AsyncSession) -> None:
    """期望必须人来标。让模型标期望再拿去评模型，等于让它自己出考卷。"""
    await seed_day(session, DAY, "随便聊聊", "好")

    case = await export_day(session, DAY)

    assert case.expect.facts == [] and case.expect.corrections == []
    assert case.expect.no_op is False
    assert "待人工标注" in case.note


async def test_export_only_takes_that_days_messages(session: AsyncSession) -> None:
    """跨天的会话只截当天那段，和整理任务看到的输入保持同一口径。"""
    conversation = await seed_day(session, DAY, "当天说的话")
    earlier = dt.datetime.combine(DAY - dt.timedelta(days=3), dt.time(12, 0)).astimezone()
    session.add(
        Message(
            conversation_id=conversation.id,
            role="user",
            content=[{"type": "text", "text": "三天前说的话"}],
            created_at=earlier,
        )
    )
    await session.commit()

    case = await export_day(session, DAY)
    texts = [m.text for c in case.conversations for m in c.messages]

    assert "当天说的话" in texts
    assert "三天前说的话" not in texts


async def test_export_drops_thinking_and_tool_blocks(session: AsyncSession) -> None:
    """样本里只留双方说的话 —— 和摘要那步看到的一致。"""
    when = dt.datetime.combine(DAY, dt.time(12, 0)).astimezone()
    conversation = Conversation(title="带工具的会话", created_at=when, updated_at=when)
    session.add(conversation)
    await session.flush()
    session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                role="user",
                content=[{"type": "text", "text": "记住我住在示例市"}],
                created_at=when,
            ),
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content=[
                    {"type": "thinking", "thinking": "内部推理", "signature": "s"},
                    {"type": "tool_use", "id": "t1", "name": "memory", "input": {}},
                ],
                created_at=when,
            ),
        ]
    )
    await session.commit()

    case = await export_day(session, DAY)
    texts = [m.text for c in case.conversations for m in c.messages]

    assert texts == ["记住我住在示例市"]


async def test_export_refuses_an_empty_day(session: AsyncSession) -> None:
    """导不出样本要报错，别产出一条没有输入的空样本埋在数据集里。"""
    with pytest.raises(ValueError, match="没有任何对话"):
        await export_day(session, DAY)


async def test_day_bounds_are_local(session: AsyncSession) -> None:
    """「那天」是本地概念。按 UTC 切会漏掉本地 00:00–08:00 的对话。"""
    start, end = local_day_bounds(DAY)
    when = dt.datetime.combine(DAY, dt.time(0, 30)).astimezone()

    assert start <= when < end
