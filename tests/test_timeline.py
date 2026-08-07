import datetime as dt

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation
from app.db.session import get_session
from app.main import create_app
from app.config import Settings
from app.memory.prompt import build_system_prompt
from app.memory.store import MemoryStore
from app.timeline.store import TimelineStore
from app.timeline.tool import TimelineToolExecutor

UTC8 = dt.timezone(dt.timedelta(hours=8))


async def test_store_creates_filters_and_updates(session: AsyncSession) -> None:
    store = TimelineStore(session, actor="manual")
    first = await store.create({
        "title": "产品会议",
        "kind": "event",
        "starts_at": dt.datetime(2026, 8, 8, 15, 0, tzinfo=UTC8),
    })
    await store.create({
        "title": "九月旅行",
        "kind": "travel",
        "starts_at": dt.datetime(2026, 9, 2, 8, 0, tzinfo=UTC8),
    })
    await session.commit()

    august = await store.list(
        start=dt.datetime(2026, 8, 1, tzinfo=UTC8),
        end=dt.datetime(2026, 9, 1, tzinfo=UTC8),
    )
    assert [item.title for item in august] == ["产品会议"]

    updated = await store.update(first.id, {"status": "completed"})
    assert updated.status == "completed"


async def test_timeline_tool_marks_uncertain_item_pending(session: AsyncSession) -> None:
    conversation = Conversation(title="计划旅行")
    session.add(conversation)
    await session.flush()
    executor = TimelineToolExecutor(
        TimelineStore(session, actor="chat", conversation_id=conversation.id)
    )

    result, is_error = await executor.execute("timeline_create", {
        "title": "可能去成都",
        "kind": "travel",
        "status": "pending",
        "starts_at": "2026-09-02T08:00:00+08:00",
    })

    assert not is_error
    assert "已创建" in result
    item = (await TimelineStore(session, actor="test").list())[0]
    assert item.status == "pending"
    assert item.source_conversation_id == conversation.id


async def test_timeline_crud_api(session: AsyncSession) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/timeline", json={
            "title": "妈妈生日",
            "kind": "birthday",
            "starts_at": "2026-08-20T00:00:00+08:00",
            "all_day": True,
            "recurrence": "yearly",
        })
        assert created.status_code == 201
        item_id = created.json()["id"]

        listed = await client.get("/api/timeline", params={
            "from": "2026-08-01T00:00:00+08:00",
            "to": "2026-09-01T00:00:00+08:00",
        })
        assert [item["title"] for item in listed.json()] == ["妈妈生日"]

        updated = await client.patch(f"/api/timeline/{item_id}", json={"status": "completed"})
        assert updated.json()["status"] == "completed"

        assert (await client.delete(f"/api/timeline/{item_id}")).status_code == 204
        assert (await client.get("/api/timeline")).json() == []


async def test_timeline_rejects_naive_datetime(session: AsyncSession) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/timeline", json={
            "title": "没有时区",
            "starts_at": "2026-08-20T10:00:00",
        })
    assert response.status_code == 400
    assert "时区" in response.json()["detail"]


async def test_chat_prompt_mentions_timeline_but_consolidation_prompt_does_not(session: AsyncSession) -> None:
    store = MemoryStore(session, actor="test")
    chat_prompt = await build_system_prompt(store, Settings())
    consolidation_prompt = await build_system_prompt(
        store, Settings(), include_kb=False, include_timeline=False
    )

    assert "# 时间线" in chat_prompt
    assert "timeline_create" not in consolidation_prompt
