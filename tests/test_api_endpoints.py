"""新增接口的端到端测试，跑在内存 SQLite 上，不需要 API key。"""

import datetime as dt
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, ConversationSummary, DailyDigest, Message
from app.db.session import get_session
from app.main import create_app
from app.memory.store import MemoryStore


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def seed_conversation(session: AsyncSession, title: str = "测试") -> Conversation:
    conversation = Conversation(title=title)
    session.add(conversation)
    await session.flush()
    return conversation


# ---------- 工具目录 ----------


async def test_tool_catalog_exposes_names_descriptions_and_schemas(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/tools")
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 8
    assert {tool["name"] for tool in body["tools"]} == {
        "memory",
        "timeline_list",
        "timeline_create",
        "timeline_update",
        "kb_search",
        "kb_read",
        "kb_list",
        "kb_backlinks",
    }
    for tool in body["tools"]:
        assert tool["description"]
        assert tool["input_schema"]["type"] == "object"
        assert "properties" in tool["input_schema"]

    memory = next(tool for tool in body["tools"] if tool["name"] == "memory")
    assert memory["native_provider"] == "anthropic"
    assert "command" in memory["input_schema"]["required"]


# ---------- 可回顾日期 ----------


async def test_review_days_only_expose_days_with_content(
    client: AsyncClient, session: AsyncSession
) -> None:
    conversation = await seed_conversation(session)
    session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                role="user",
                content=[{"type": "text", "text": "发生过"}],
                created_at=dt.datetime(2026, 8, 5, 12, tzinfo=dt.UTC),
            ),
            DailyDigest(
                day=dt.date(2026, 8, 3),
                headline="保留下来的回顾",
                highlights=[],
            ),
        ]
    )
    await session.commit()

    assert (await client.get("/api/review/days")).json() == [
        "2026-08-05",
        "2026-08-03",
    ]


# ---------- 消息里的 usage ----------


async def test_messages_expose_usage(client: AsyncClient, session: AsyncSession) -> None:
    conversation = await seed_conversation(session)
    session.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=[{"type": "text", "text": "答"}],
            usage={"prompt_tokens": 100, "completion_tokens": 20},
        )
    )
    await session.commit()

    body = (await client.get(f"/api/conversations/{conversation.id}/messages")).json()
    assert body[0]["usage"]["prompt_tokens"] == 100


# ---------- 截断 ----------


async def test_truncate_after_message(client: AsyncClient, session: AsyncSession) -> None:
    conversation = await seed_conversation(session)
    ids = []
    for i in range(4):
        m = Message(
            conversation_id=conversation.id,
            role="user" if i % 2 == 0 else "assistant",
            content=[{"type": "text", "text": f"m{i}"}],
        )
        session.add(m)
        await session.flush()
        ids.append(m.id)
    await session.commit()

    resp = await client.delete(
        f"/api/conversations/{conversation.id}/messages", params={"after": ids[1]}
    )
    assert resp.json() == {"deleted": 2}

    remaining = (
        await client.get(f"/api/conversations/{conversation.id}/messages")
    ).json()
    assert [m["id"] for m in remaining] == ids[:2]


async def test_truncate_all_with_after_zero(
    client: AsyncClient, session: AsyncSession
) -> None:
    conversation = await seed_conversation(session)
    session.add(
        Message(
            conversation_id=conversation.id,
            role="user",
            content=[{"type": "text", "text": "x"}],
        )
    )
    await session.commit()

    assert (
        await client.delete(f"/api/conversations/{conversation.id}/messages")
    ).json() == {"deleted": 1}


async def test_truncate_missing_conversation_404(client: AsyncClient) -> None:
    assert (await client.delete("/api/conversations/9999/messages")).status_code == 404


# ---------- 归档 ----------


async def test_archive_hides_from_default_list(
    client: AsyncClient, session: AsyncSession
) -> None:
    conversation = await seed_conversation(session, "要归档的")
    await session.commit()

    assert len((await client.get("/api/conversations")).json()) == 1

    await client.post(f"/api/conversations/{conversation.id}/archive")
    assert (await client.get("/api/conversations")).json() == []

    archived = (await client.get("/api/conversations", params={"archived": True})).json()
    assert [c["title"] for c in archived] == ["要归档的"]


async def test_unarchive_restores(client: AsyncClient, session: AsyncSession) -> None:
    conversation = await seed_conversation(session)
    await session.commit()

    await client.post(f"/api/conversations/{conversation.id}/archive")
    await client.post(
        f"/api/conversations/{conversation.id}/archive", params={"archived": False}
    )
    assert len((await client.get("/api/conversations")).json()) == 1


# ---------- 会话摘要 ----------


async def test_summaries_include_conversation_title(
    client: AsyncClient, session: AsyncSession
) -> None:
    conversation = await seed_conversation(session, "聊了 uv")
    session.add(
        ConversationSummary(
            conversation_id=conversation.id,
            summary="用户说他用 uv",
            up_to_message_id=1,
        )
    )
    await session.commit()

    body = (await client.get("/api/summaries")).json()
    assert body[0]["conversation_title"] == "聊了 uv"
    assert body[0]["summary"] == "用户说他用 uv"


async def test_summaries_filter_by_conversation(
    client: AsyncClient, session: AsyncSession
) -> None:
    first = await seed_conversation(session, "一")
    second = await seed_conversation(session, "二")
    session.add_all(
        [
            ConversationSummary(conversation_id=first.id, summary="A", up_to_message_id=1),
            ConversationSummary(conversation_id=second.id, summary="B", up_to_message_id=1),
        ]
    )
    await session.commit()

    body = (
        await client.get("/api/summaries", params={"conversation_id": second.id})
    ).json()
    assert [s["summary"] for s in body] == ["B"]


# ---------- 全局记忆变更 ----------


async def test_global_versions_route_is_not_shadowed(
    client: AsyncClient, session: AsyncSession
) -> None:
    """/versions 必须命中全局路由，而不是被 /{path:path} 吃掉。

    path 是贪婪匹配，两个路由的声明顺序反了就会静默失效。
    """
    store = MemoryStore(session, actor="chat")
    await store.create("/memories/a.md", "内容")
    await session.commit()

    resp = await client.get("/api/memories/versions")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert resp.json()[0]["path"] == "/memories/a.md"


async def test_global_versions_includes_deleted_memories(
    client: AsyncClient, session: AsyncSession
) -> None:
    """删掉的记忆路径已不在树里，按路径查根本构造不出 URL —— 只能从这里看到。"""
    store = MemoryStore(session, actor="chat")
    await store.create("/memories/gone.md", "曾经的内容")
    await store.delete("/memories/gone.md")
    await session.commit()

    body = (await client.get("/api/memories/versions")).json()
    deleted = [v for v in body if v["operation"] == "deleted"]
    assert deleted[0]["path"] == "/memories/gone.md"
    assert deleted[0]["content"] == "曾经的内容"  # 内容可回收，能做「恢复」


async def test_global_versions_filter_by_actor(
    client: AsyncClient, session: AsyncSession
) -> None:
    await MemoryStore(session, actor="chat").create("/memories/a.md", "1")
    await MemoryStore(session, actor="consolidation").create("/memories/b.md", "2")
    await session.commit()

    body = (
        await client.get("/api/memories/versions", params={"actor": "consolidation"})
    ).json()
    assert [v["path"] for v in body] == ["/memories/b.md"]


async def test_per_path_versions_still_works(
    client: AsyncClient, session: AsyncSession
) -> None:
    store = MemoryStore(session, actor="manual")
    await store.create("/memories/profile/x.md", "v1")
    await store.create("/memories/profile/x.md", "v2")
    await session.commit()

    body = (await client.get("/api/memories/profile/x.md/versions")).json()
    assert [v["operation"] for v in body] == ["modified", "created"]


# ---------- 用量 ----------


async def test_daily_usage_normalizes_provider_field_names(
    client: AsyncClient, session: AsyncSession
) -> None:
    conversation = await seed_conversation(session)
    session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content=[],
                usage={"prompt_tokens": 100, "completion_tokens": 20,
                       "prompt_cache_hit_tokens": 60},
            ),
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content=[],
                usage={"input_tokens": 5, "output_tokens": 3,
                       "cache_read_input_tokens": 2},
            ),
        ]
    )
    await session.commit()

    today = (await client.get("/api/usage", params={"days": 1})).json()[0]
    assert today["input_tokens"] == 105  # DeepSeek 和 Anthropic 的字段名都算进来
    assert today["output_tokens"] == 23
    assert today["cached_tokens"] == 62
    assert today["messages"] == 2


async def test_daily_usage_runs_one_query_regardless_of_window(
    client: AsyncClient, session: AsyncSession
) -> None:
    """按天循环查库的话，days=90 就是 90 次往返。窗口长度不该改变查询次数。"""
    conversation = await seed_conversation(session)
    session.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=[],
            usage={"input_tokens": 7, "output_tokens": 1},
        )
    )
    await session.commit()

    statements: list[str] = []

    @event.listens_for(session.sync_session, "do_orm_execute")
    def record(orm_execute_state) -> None:
        if orm_execute_state.is_select:
            statements.append(str(orm_execute_state.statement))

    try:
        response = await client.get("/api/usage", params={"days": 90})
    finally:
        event.remove(session.sync_session, "do_orm_execute", record)

    assert response.status_code == 200
    messages_query = [s for s in statements if "FROM messages" in s]
    assert len(messages_query) == 1, f"预期 1 条消息查询，实际 {len(messages_query)} 条"


async def test_daily_usage_covers_every_day_in_the_window(
    client: AsyncClient, session: AsyncSession
) -> None:
    """没有消息的日子也要出现 —— 前端按天画图，缺天会错位。"""
    body = (await client.get("/api/usage", params={"days": 7})).json()

    assert len(body) == 7
    assert body[0]["day"] > body[-1]["day"]  # 今天在最前面
    assert all(row["messages"] == 0 for row in body)


async def test_daily_usage_rejects_nonpositive_window(client: AsyncClient) -> None:
    assert (await client.get("/api/usage", params={"days": 0})).json() == []


# ---------- 记忆回滚 ----------


async def test_restore_previous_version(
    client: AsyncClient, session: AsyncSession
) -> None:
    store = MemoryStore(session, actor="chat")
    await store.create("/memories/a.md", "第一版")
    await store.create("/memories/a.md", "第二版（写错了）")
    await session.commit()

    versions = (await client.get("/api/memories/a.md/versions")).json()
    first = next(v for v in versions if v["content"] == "第一版")

    body = (
        await client.post("/api/memories/restore", json={"version_id": first["id"]})
    ).json()
    assert body["content"] == "第一版"


async def test_restore_deleted_memory(
    client: AsyncClient, session: AsyncSession
) -> None:
    """删掉的记忆路径已不在树里，只能靠 version_id 恢复 —— 这正是不按路径定位的原因。"""
    store = MemoryStore(session, actor="chat")
    await store.create("/memories/gone.md", "误删的内容")
    await store.delete("/memories/gone.md")
    await session.commit()

    assert (await client.get("/api/memories/gone.md")).status_code == 404

    deleted = next(
        v
        for v in (await client.get("/api/memories/versions")).json()
        if v["operation"] == "deleted"
    )
    await client.post("/api/memories/restore", json={"version_id": deleted["id"]})

    body = (await client.get("/api/memories/gone.md")).json()
    assert body["content"] == "误删的内容"


async def test_restore_records_a_new_version(
    client: AsyncClient, session: AsyncSession
) -> None:
    """恢复本身也留痕，所以回滚可以再回滚。"""
    store = MemoryStore(session, actor="chat")
    await store.create("/memories/a.md", "v1")
    await store.create("/memories/a.md", "v2")
    await session.commit()

    v1 = next(
        v for v in (await client.get("/api/memories/a.md/versions")).json()
        if v["content"] == "v1"
    )
    await client.post("/api/memories/restore", json={"version_id": v1["id"]})

    versions = (await client.get("/api/memories/a.md/versions")).json()
    assert versions[0]["actor"] == "manual"
    assert versions[0]["content"] == "v1"


async def test_restore_missing_version_404(client: AsyncClient) -> None:
    resp = await client.post("/api/memories/restore", json={"version_id": 99999})
    assert resp.status_code == 404


# ---------- 索引校验 ----------


async def test_audit_reports_a_memory_missing_from_the_index(
    client: AsyncClient, session: AsyncSession
) -> None:
    store = MemoryStore(session, actor="manual")
    await store.create("/memories/MEMORY.md", "- [身份](profile/identity.md) — 基本信息")
    await store.create("/memories/profile/identity.md", "- 住在示例市")
    await store.create("/memories/projects/chat.md", "- 个人助手项目")

    body = (await client.get("/api/memories/audit")).json()

    assert body["ok"] is False
    assert body["missing"] == ["/memories/projects/chat.md"]
    assert body["total_files"] == 2


async def test_audit_route_is_not_shadowed_by_the_path_route(
    client: AsyncClient, session: AsyncSession
) -> None:
    """`/audit` 必须声明在 `/{path:path}` 之前，否则会被当成一条记忆路径去查。"""
    response = await client.get("/api/memories/audit")

    assert response.status_code == 200
    assert "issue_count" in response.json()
