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


# --- 逾期不能静默消失 ---------------------------------------------------------


async def test_overdue_items_surface_in_todays_window(session: AsyncSession) -> None:
    """「今天」视图曾经严格按 [今天00:00, 明天00:00) 查。

    结果昨天没勾掉的事从今天和最近视图里一起消失，只有翻月历才找得回来 ——
    加上通知漏发，这件事就是真的丢了。
    """
    store = TimelineStore(session, actor="manual")
    yesterday = dt.datetime(2026, 8, 6, 10, 0, tzinfo=UTC8)
    today_start = dt.datetime(2026, 8, 7, 0, 0, tzinfo=UTC8)
    tomorrow = dt.datetime(2026, 8, 8, 0, 0, tzinfo=UTC8)

    await store.create({"title": "昨天没做完", "starts_at": yesterday})
    await store.create({"title": "昨天做完了", "starts_at": yesterday, "status": "completed"})
    await store.create({"title": "今天的事", "starts_at": dt.datetime(2026, 8, 7, 15, 0, tzinfo=UTC8)})
    await session.commit()

    strict = await store.list(start=today_start, end=tomorrow)
    assert [item.title for item in strict] == ["今天的事"]

    with_overdue = await store.list(start=today_start, end=tomorrow, include_overdue=True)
    # 逾期的排在前面（按开始时间），已完成的不算逾期。
    assert [item.title for item in with_overdue] == ["昨天没做完", "今天的事"]


# --- 每年重复必须真的重复 -----------------------------------------------------


async def test_yearly_item_appears_in_later_years(session: AsyncSession) -> None:
    """recurrence=yearly 过去只用来在卡片上印「每年重复」四个字。

    查询是纯 starts_at 范围过滤，所以 2026 年记的生日在 2027 年的月历里
    什么都不显示。
    """
    store = TimelineStore(session, actor="manual")
    await store.create({
        "title": "妈妈生日",
        "kind": "birthday",
        "starts_at": dt.datetime(2026, 8, 20, 0, 0, tzinfo=UTC8),
        "all_day": True,
        "recurrence": "yearly",
    })
    await session.commit()

    for year in (2026, 2027, 2030):
        window = await store.list(
            start=dt.datetime(year, 8, 1, tzinfo=UTC8),
            end=dt.datetime(year, 9, 1, tzinfo=UTC8),
        )
        assert [item.title for item in window] == ["妈妈生日"], f"{year} 年没查到"
        assert window[0].starts_at.year == year

    # 不重复的事项不该被展开到别的年份。
    await store.create({
        "title": "一次性会议",
        "starts_at": dt.datetime(2026, 8, 21, 10, 0, tzinfo=UTC8),
    })
    await session.commit()
    next_year = await store.list(
        start=dt.datetime(2027, 8, 1, tzinfo=UTC8),
        end=dt.datetime(2027, 9, 1, tzinfo=UTC8),
    )
    assert [item.title for item in next_year] == ["妈妈生日"]


async def test_yearly_projection_does_not_rewrite_the_stored_row(session: AsyncSession) -> None:
    """展开出来的是副本。改原对象会在 flush 时把生日永久挪到当年。"""
    store = TimelineStore(session, actor="manual")
    item = await store.create({
        "title": "纪念日",
        "starts_at": dt.datetime(2026, 3, 5, 0, 0, tzinfo=UTC8),
        "all_day": True,
        "recurrence": "yearly",
    })
    await session.commit()
    item_id = item.id

    projected = await store.list(
        start=dt.datetime(2029, 1, 1, tzinfo=UTC8),
        end=dt.datetime(2030, 1, 1, tzinfo=UTC8),
    )
    assert [entry.starts_at.year for entry in projected] == [2029]
    await session.commit()

    # 库里那一行还停在原来的年份。（SQLite 会抹掉时区，所以只比日期部分。）
    await session.refresh(item)
    stored = await store.get(item_id)
    assert (stored.starts_at.year, stored.starts_at.month, stored.starts_at.day) == (2026, 3, 5)


async def test_leap_day_falls_back_to_february_28(session: AsyncSession) -> None:
    store = TimelineStore(session, actor="manual")
    await store.create({
        "title": "闰日纪念",
        "starts_at": dt.datetime(2028, 2, 29, 0, 0, tzinfo=UTC8),
        "all_day": True,
        "recurrence": "yearly",
    })
    await session.commit()

    window = await store.list(
        start=dt.datetime(2027, 2, 1, tzinfo=UTC8),
        end=dt.datetime(2027, 3, 1, tzinfo=UTC8),
    )
    assert [item.starts_at.day for item in window] == [28]


async def test_snooze_endpoint(session: AsyncSession) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/timeline", json={
            "title": "体检", "kind": "event", "starts_at": "2026-08-20T09:00:00+08:00",
        })
        item_id = created.json()["id"]
        assert created.json()["remind_at"] is not None

        snoozed = await client.post(f"/api/timeline/{item_id}/snooze", json={"minutes": 30})
        assert snoozed.status_code == 200
        assert snoozed.json()["snoozed_until"] is not None

        # 关掉提醒后 remind_at 必须清空 —— 留着旧值 ticker 还会扫到。
        muted = await client.patch(f"/api/timeline/{item_id}", json={"notify": False})
        assert muted.json()["remind_at"] is None


async def test_notify_status_and_test_endpoints(session: AsyncSession) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        status_body = (await client.get("/api/notify/status")).json()
        assert [c["name"] for c in status_body["channels"]] == ["bark"]
        # 默认没配 Bark key，必须如实报「没配好」而不是一个笼统的 ok。
        assert status_body["ready"] is False
        assert status_body["channels"][0]["configured"] is False

        result = (await client.post("/api/notify/test")).json()
        assert result["delivered"] is False
        assert "没有配置好的通知通道" in result["error"]


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


async def test_explicit_relative_duration_passes(session: AsyncSession) -> None:
    """「一分钟后」是可计算的明确时刻，不应被当成「中午」那类模糊时间。"""
    executor = await _executor(session)

    for said in ("一分钟后", "10分钟以后", "半小时之后", "in 5 minutes", "after 2 hours"):
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
    assert "一分钟后" in TIMELINE_INSTRUCTIONS
    assert "第一次消息里直接处理" in TIMELINE_INSTRUCTIONS
