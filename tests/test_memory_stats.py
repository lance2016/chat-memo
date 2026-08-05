"""记忆使用率埋点与统计。

要回答的问题：攒的这些记忆到底有没有被用上、哪些是噪音。
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MemoryRead
from app.db.session import get_session
from app.main import create_app
from app.memory.errors import MemoryNotFound
from app.memory.stats import collect_stats
from app.memory.store import MemoryStore


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def reads_of(session: AsyncSession) -> list[MemoryRead]:
    return list((await session.execute(select(MemoryRead))).scalars())


# ---------- 埋点 ----------


async def test_view_records_a_read(session: AsyncSession) -> None:
    store = MemoryStore(session, actor="chat", conversation_id=7)
    await store.create("/memories/a.md", "内容")
    await store.view("/memories/a.md")

    rows = await reads_of(session)
    assert len(rows) == 1
    assert rows[0].path == "/memories/a.md"
    assert rows[0].actor == "chat"
    assert rows[0].conversation_id == 7
    assert rows[0].found is True


async def test_write_commands_do_not_count_as_reads(session: AsyncSession) -> None:
    """只有 view 算「用上了」，写入不算 —— 否则模型自己写的会把统计顶满。"""
    store = MemoryStore(session, actor="chat")
    await store.create("/memories/a.md", "1")
    await store.str_replace("/memories/a.md", "1", "2")
    await store.insert("/memories/a.md", 0, "x")

    assert await reads_of(session) == []


async def test_missing_path_records_a_miss(session: AsyncSession) -> None:
    """模型读了个不存在的路径 —— 索引和实际内容对不上的信号。"""
    store = MemoryStore(session, actor="chat")
    with pytest.raises(MemoryNotFound):
        await store.view("/memories/nope.md")

    rows = await reads_of(session)
    assert len(rows) == 1 and rows[0].found is False


async def test_directory_listing_counts_as_read(session: AsyncSession) -> None:
    store = MemoryStore(session, actor="chat")
    await store.create("/memories/profile/a.md", "x")
    await store.view("/memories/profile")

    rows = await reads_of(session)
    assert [r.path for r in rows] == ["/memories/profile"]


async def test_api_browsing_is_not_tracked(
    client: AsyncClient, session: AsyncSession
) -> None:
    """在记忆页翻看不等于「模型用了这条记忆」，计进去统计就废了。"""
    await MemoryStore(session, actor="chat").create("/memories/a.md", "内容")
    await session.commit()

    await client.get("/api/memories/a.md")
    await client.get("/api/memories")

    assert await reads_of(session) == []


# ---------- 统计 ----------


async def test_stats_counts_reads_and_writes(session: AsyncSession) -> None:
    store = MemoryStore(session, actor="chat")
    await store.create("/memories/hot.md", "热门")
    await store.create("/memories/cold.md", "冷门")
    for _ in range(3):
        await store.view("/memories/hot.md")
    await session.commit()

    stats = await collect_stats(session)
    assert stats.total_memories == 2
    assert stats.total_reads == 3
    assert stats.never_read == 1
    assert stats.top[0].path == "/memories/hot.md"
    assert stats.top[0].reads == 3
    assert [u.path for u in stats.unused] == ["/memories/cold.md"]


async def test_index_excluded_from_ranking(session: AsyncSession) -> None:
    """MEMORY.md 每轮都自动进 system prompt，和「模型主动去读」不是一回事。"""
    store = MemoryStore(session, actor="chat")
    await store.create("/memories/MEMORY.md", "索引")
    await store.create("/memories/a.md", "内容")
    await store.view("/memories/MEMORY.md")
    await store.view("/memories/a.md")
    await session.commit()

    stats = await collect_stats(session)
    assert [u.path for u in stats.top] == ["/memories/a.md"]
    assert all(u.path != "/memories/MEMORY.md" for u in stats.unused)


async def test_stats_tracks_misses(session: AsyncSession) -> None:
    store = MemoryStore(session, actor="chat")
    with pytest.raises(MemoryNotFound):
        await store.view("/memories/ghost.md")
    await session.commit()

    assert (await collect_stats(session)).missed_reads == 1


async def test_stats_by_actor(session: AsyncSession) -> None:
    await MemoryStore(session, actor="chat").create("/memories/a.md", "x")
    consolidation = MemoryStore(session, actor="consolidation")
    await consolidation.view("/memories/a.md")
    await session.commit()

    breakdown = {b.actor: b for b in (await collect_stats(session)).by_actor}
    assert breakdown["chat"].writes == 1
    assert breakdown["consolidation"].reads == 1


async def test_stats_daily_series_is_gap_free(session: AsyncSession) -> None:
    """没有活动的日子也要返回 0，前端画图不用自己补空洞。"""
    stats = await collect_stats(session, days=7)
    assert len(stats.daily) == 7
    assert stats.daily[0].day < stats.daily[-1].day  # 升序，方便直接画折线


async def test_unused_sorted_by_length_not_age(session: AsyncSession) -> None:
    """从未被打开的按内容长度倒序。

    短记忆没被 view 是正常的 —— 索引里那行摘要（「住在示例市」）已经答完了问题。
    真正可疑的是写了一大堆细节却从没被用到的。
    """
    store = MemoryStore(session, actor="chat")
    await store.create("/memories/short.md", "短")
    await store.create("/memories/long.md", "很长的内容" * 50)
    await session.commit()

    stats = await collect_stats(session)
    assert [u.path for u in stats.unused] == ["/memories/long.md", "/memories/short.md"]
    assert stats.unused[0].content_chars > stats.unused[1].content_chars


# ---------- 接口 ----------


async def test_stats_endpoint_not_shadowed_by_path_route(
    client: AsyncClient, session: AsyncSession
) -> None:
    """/stats 必须命中统计路由，不能被 /{path:path} 吃掉。"""
    await MemoryStore(session, actor="chat").create("/memories/a.md", "x")
    await session.commit()

    resp = await client.get("/api/memories/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_memories"] == 1
    assert "daily" in body and "top" in body and "unused" in body


async def test_stats_endpoint_idle_days(
    client: AsyncClient, session: AsyncSession
) -> None:
    store = MemoryStore(session, actor="chat")
    await store.create("/memories/a.md", "x")
    await store.view("/memories/a.md")
    await session.commit()

    body = (await client.get("/api/memories/stats")).json()
    assert body["top"][0]["idle_days"] == 0
    assert body["unused"] == []
