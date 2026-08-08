"""编辑重发/重新生成走软删除，不真删行。

这个文件钉的不是某一个函数，而是一条**跨模块的约定**：撤下的消息在所有「对话历史」
读取点都必须消失，但行要留着。约定散在七八个查询里，只有整组测试摆在一起才看得出
漏了哪个 —— 所以刻意不按模块拆进各自的测试文件。
"""

import datetime as dt
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.service import ChatService
from app.config import Settings
from app.db.models import Conversation, Message
from app.db.session import get_session
from app.jobs import backfill
from app.jobs.consolidate import Consolidator
from app.llm.anthropic_provider import AnthropicProvider
from app.main import create_app
from app.search import search as run_search
from tests.fakes import FakeAnthropic, text_turn


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def seed(
    session: AsyncSession, texts: list[str], *, when: dt.datetime | None = None
) -> tuple[Conversation, list[int]]:
    conversation = Conversation(title="测试")
    session.add(conversation)
    await session.flush()

    ids = []
    for index, text in enumerate(texts):
        message = Message(
            conversation_id=conversation.id,
            role="user" if index % 2 == 0 else "assistant",
            content=[{"type": "text", "text": text}],
            search_text=text,
        )
        if when is not None:
            message.created_at = when
        session.add(message)
        await session.flush()
        ids.append(message.id)
    await session.commit()
    return conversation, ids


async def test_truncate_keeps_rows_in_db(
    client: AsyncClient, session: AsyncSession
) -> None:
    """接口说删了 2 条，库里还是 4 行 —— 这就是这次改动的全部意义。"""
    conversation, ids = await seed(session, ["m0", "m1", "m2", "m3"])

    resp = await client.delete(
        f"/api/conversations/{conversation.id}/messages", params={"after": ids[1]}
    )
    assert resp.json() == {"deleted": 2}

    total = await session.scalar(
        select(func.count(Message.id)).where(Message.conversation_id == conversation.id)
    )
    assert total == 4

    live = (await client.get(f"/api/conversations/{conversation.id}/messages")).json()
    assert [m["id"] for m in live] == ids[:2]

    withdrawn = (
        await session.execute(
            select(Message).where(Message.id.in_(ids[2:]))
        )
    ).scalars()
    assert all(m.deleted_at is not None for m in withdrawn)


async def test_truncate_twice_counts_only_new_withdrawals(
    client: AsyncClient, session: AsyncSession
) -> None:
    """已经撤下的不再重复计数，否则前端会以为又删掉了一批。"""
    conversation, ids = await seed(session, ["m0", "m1", "m2"])
    url = f"/api/conversations/{conversation.id}/messages"

    assert (await client.delete(url, params={"after": ids[0]})).json() == {"deleted": 2}
    assert (await client.delete(url, params={"after": ids[0]})).json() == {"deleted": 0}


async def test_history_sent_to_model_excludes_withdrawn(
    client: AsyncClient, session: AsyncSession
) -> None:
    """最要紧的一条：撤下的内容不能再回到模型的上下文里。"""
    conversation, ids = await seed(session, ["记住我喜欢喝美式", "好的"])
    await client.delete(
        f"/api/conversations/{conversation.id}/messages", params={"after": 0}
    )

    service = ChatService(
        session,
        AnthropicProvider(settings=Settings(anthropic_api_key="test"), client=FakeAnthropic([])),
    )
    assert await service.load_history(conversation.id) == []


async def test_search_skips_withdrawn(
    client: AsyncClient, session: AsyncSession
) -> None:
    conversation, _ = await seed(session, ["独特的关键词"])
    assert (await run_search(session, "独特的关键词", 10)).conversations

    await client.delete(f"/api/conversations/{conversation.id}/messages")
    assert (await run_search(session, "独特的关键词", 10)).conversations == []


async def test_day_with_only_withdrawn_messages_is_not_worth_consolidating(
    client: AsyncClient, session: AsyncSession
) -> None:
    """整条撤下的日子不该再触发一次 agent loop。"""
    day = dt.date.today() - dt.timedelta(days=2)
    when = dt.datetime.combine(day, dt.time(12, 0)).astimezone()
    conversation, _ = await seed(session, ["聊了点什么"], when=when)
    settings = Settings(anthropic_api_key="test", consolidate_hour=4)

    assert day in await backfill.pending_days(session, settings)

    await client.delete(f"/api/conversations/{conversation.id}/messages")
    assert day not in await backfill.pending_days(session, settings)


async def test_withdrawn_messages_never_reach_the_summarizer(
    client: AsyncClient, session: AsyncSession
) -> None:
    """撤下的内容不能进摘要 —— 进了就顺着摘要一路写进 L2，再也撤不回来。"""
    conversation, ids = await seed(session, ["我要辞职", "嗯", "刚才说的不算", "好"])
    await client.delete(
        f"/api/conversations/{conversation.id}/messages", params={"after": ids[1]}
    )

    fake = FakeAnthropic([text_turn("用户提到要辞职"), text_turn("已整理")])
    await Consolidator(
        session,
        AnthropicProvider(settings=Settings(anthropic_api_key="test"), client=fake),
    ).run(dt.date.today())

    # 第一次调用是摘要，它拿到的 transcript 就是喂给记忆链路的全部原料
    transcript = str(fake.messages.calls[0]["messages"])
    assert "我要辞职" in transcript
    assert "刚才说的不算" not in transcript


async def test_usage_stats_still_count_withdrawn_messages(
    client: AsyncClient, session: AsyncSession
) -> None:
    """故意的例外：那些 token 是真花掉了的，抹掉会让账单对不上。"""
    conversation = Conversation(title="测试")
    session.add(conversation)
    await session.flush()
    session.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=[{"type": "text", "text": "答"}],
            usage={"input_tokens": 100, "output_tokens": 20},
        )
    )
    await session.commit()

    await client.delete(f"/api/conversations/{conversation.id}/messages")

    today = dt.date.today().isoformat()
    usage = (await client.get("/api/usage")).json()
    hit = next(row for row in usage if row["day"] == today)
    assert hit["input_tokens"] == 100
    assert hit["output_tokens"] == 20
