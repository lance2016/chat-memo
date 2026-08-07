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
        "said": "9 月 2 号早上八点",
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


# --- 模糊时间必须先追问，不能自己挑一个 ----------------------------------------


async def _executor(session: AsyncSession) -> TimelineToolExecutor:
    return TimelineToolExecutor(TimelineStore(session, actor="chat"))


async def test_vague_time_is_rejected_instead_of_guessed(session: AsyncSession) -> None:
    """「今天中午」曾经被填成凭空捏造的 11:20 并标成 confirmed。

    提示词里那句「有歧义时宁可 pending」实测拦不住，所以改成硬校验：
    工具报错后模型拿到 is_error 的 tool_result，自然会回头问用户。
    """
    executor = await _executor(session)

    result, is_error = await executor.execute("timeline_create", {
        "title": "点午饭外卖",
        "kind": "reminder",
        "status": "confirmed",
        "starts_at": "2026-08-07T11:20:00+08:00",
        "said": "今天中午",
    })

    assert is_error
    assert "没有具体钟点" in result
    assert "先问用户" in result
    assert await TimelineStore(session, actor="test").list() == []


async def test_missing_said_is_rejected(session: AsyncSession) -> None:
    """said 留空 = 用户根本没提时间，那更不该凭空定一个。"""
    executor = await _executor(session)

    result, is_error = await executor.execute("timeline_create", {
        "title": "交周报",
        "kind": "todo",
        "status": "confirmed",
        "starts_at": "2026-08-07T18:00:00+08:00",
    })

    assert is_error
    assert "缺少 said" in result


async def test_explicit_clock_passes(session: AsyncSession) -> None:
    executor = await _executor(session)

    for said in ("明早九点", "下午六点", "18:00 出发", "tomorrow 6pm", "九点半", "明天中午12点"):
        result, is_error = await executor.execute("timeline_create", {
            "title": f"测试 {said}",
            "kind": "reminder",
            "status": "confirmed",
            "starts_at": "2026-08-08T09:00:00+08:00",
            "said": said,
        })
        assert not is_error, f"{said} 不该被拒绝：{result}"


async def test_all_day_escapes_the_clock_requirement(session: AsyncSession) -> None:
    """整天有效的事项本来就没有钟点，也是「随便你定」时的出口。"""
    executor = await _executor(session)

    result, is_error = await executor.execute("timeline_create", {
        "title": "叶顺英生日",
        "kind": "birthday",
        "status": "confirmed",
        "starts_at": "2026-09-12T00:00:00+08:00",
        "all_day": True,
        "said": "九月十二号",
    })

    assert not is_error


async def test_said_is_stored_as_evidence(session: AsyncSession) -> None:
    """starts_at 是解析结果，said 是依据 —— 时间不对时能分辨是谁的锅。"""
    executor = await _executor(session)

    await executor.execute("timeline_create", {
        "title": "去盒马买菜",
        "kind": "todo",
        "status": "confirmed",
        "starts_at": "2026-08-08T09:00:00+08:00",
        "said": "明早九点",
    })

    item = (await TimelineStore(session, actor="test").list())[0]
    assert item.said == "明早九点"


async def test_reschedule_to_a_vague_time_is_rejected(session: AsyncSession) -> None:
    executor = await _executor(session)
    await executor.execute("timeline_create", {
        "title": "产品会议", "kind": "event", "status": "confirmed",
        "starts_at": "2026-08-08T15:00:00+08:00", "said": "八号下午三点",
    })
    item = (await TimelineStore(session, actor="test").list())[0]

    result, is_error = await executor.execute("timeline_update", {
        "id": item.id,
        "starts_at": "2026-08-09T14:00:00+08:00",
        "said": "改到第二天下午",
    })

    assert is_error
    assert "没有具体钟点" in result


async def test_update_without_time_change_is_not_blocked(session: AsyncSession) -> None:
    """标记完成、改标题不该被时间规则挡住。"""
    executor = await _executor(session)
    await executor.execute("timeline_create", {
        "title": "产品会议", "kind": "event", "status": "confirmed",
        "starts_at": "2026-08-08T15:00:00+08:00", "said": "八号下午三点",
    })
    item = (await TimelineStore(session, actor="test").list())[0]

    result, is_error = await executor.execute("timeline_update", {
        "id": item.id, "status": "completed",
    })

    assert not is_error


def test_prompt_tells_model_to_ask_about_vague_times() -> None:
    from app.memory.prompt import TIMELINE_INSTRUCTIONS

    assert "先问清楚大概几点" in TIMELINE_INSTRUCTIONS
    assert "不要自己挑一个填进去" in TIMELINE_INSTRUCTIONS
