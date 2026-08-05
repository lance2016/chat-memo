"""全局搜索。

中文子串匹配，不分词。重点验证：搜索只看正文（不搜 thinking/工具参数）、
按会话聚合、通配符被转义。
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.service import extract_text
from app.db.models import Conversation, Message
from app.db.session import get_session
from app.main import create_app
from app.memory.store import MemoryStore
from app.search import make_snippet, search


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def seed(session: AsyncSession, title: str, *texts: str) -> Conversation:
    conversation = Conversation(title=title)
    session.add(conversation)
    await session.flush()
    for i, text in enumerate(texts):
        content = [{"type": "text", "text": text}]
        session.add(
            Message(
                conversation_id=conversation.id,
                role="user" if i % 2 == 0 else "assistant",
                content=content,
                search_text=extract_text(content),
            )
        )
    await session.commit()
    return conversation


# ---------- search_text 抽取 ----------


def test_extract_text_only_takes_text_blocks() -> None:
    """thinking 和工具参数不该进搜索索引 —— 搜「示例市」不该命中模型的内部推理。"""
    blocks = [
        {"type": "thinking", "thinking": "用户提到了示例市这个地方"},
        {"type": "tool_use", "id": "c1", "name": "memory", "input": {"path": "/x"}},
        {"type": "text", "text": "好的，记下了"},
    ]
    assert extract_text(blocks) == "好的，记下了"


def test_extract_text_joins_multiple_blocks() -> None:
    blocks = [{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}]
    assert extract_text(blocks) == "第一段\n第二段"


# ---------- 中文搜索 ----------


async def test_finds_chinese_substring(session: AsyncSession) -> None:
    await seed(session, "示例市话题", "我住在示例市", "记住了")

    results = await search(session, "示例市")
    assert len(results.conversations) == 1
    assert results.conversations[0].title == "示例市话题"
    assert "示例市" in results.conversations[0].snippet


async def test_finds_english_and_is_case_insensitive(session: AsyncSession) -> None:
    await seed(session, "工具", "我用 UV 管理依赖")
    assert len((await search(session, "uv")).conversations) == 1


async def test_no_match_returns_empty(session: AsyncSession) -> None:
    await seed(session, "无关", "完全不相干的内容")
    results = await search(session, "量子计算")
    assert results.conversations == [] and results.memories == []


async def test_short_query_rejected(session: AsyncSession) -> None:
    """单字查询会命中几乎所有内容，直接拒掉。"""
    await seed(session, "话题", "我住在示例市")
    assert (await search(session, "我")).conversations == []
    assert (await search(session, " ")).conversations == []


# ---------- 按会话聚合 ----------


async def test_groups_by_conversation_with_match_count(session: AsyncSession) -> None:
    """一个会话里命中多条，应该是一个结果 + 计数，而不是多行重复。"""
    await seed(session, "反复提到", "示例市天气不错", "示例市确实好", "示例市的房价呢")

    results = await search(session, "示例市")
    assert len(results.conversations) == 1
    assert results.conversations[0].matches == 3


async def test_multiple_conversations_returned(session: AsyncSession) -> None:
    await seed(session, "会话一", "聊聊示例市")
    await seed(session, "会话二", "示例市怎么样")

    titles = {c.title for c in (await search(session, "示例市")).conversations}
    assert titles == {"会话一", "会话二"}


async def test_limit_applies_to_conversations(session: AsyncSession) -> None:
    for i in range(5):
        await seed(session, f"会话{i}", "都提到了示例市")

    assert len((await search(session, "示例市", limit=2)).conversations) == 2


# ---------- 记忆搜索 ----------


async def test_searches_memories_too(session: AsyncSession) -> None:
    await MemoryStore(session, actor="chat").create(
        "/memories/profile/location.md", "住在示例市，某某区"
    )
    await session.commit()

    results = await search(session, "示例市")
    assert [m.path for m in results.memories] == ["/memories/profile/location.md"]
    assert "示例市" in results.memories[0].snippet


# ---------- 通配符转义 ----------


@pytest.mark.parametrize("wildcard", ["%", "_", "%%"])
async def test_like_wildcards_are_escaped(
    session: AsyncSession, wildcard: str
) -> None:
    """不转义的话搜 % 会匹配到全部内容。"""
    await seed(session, "普通", "一段没有特殊字符的内容")
    assert (await search(session, wildcard + "x")).conversations == []


async def test_literal_percent_is_findable(session: AsyncSession) -> None:
    await seed(session, "百分号", "缓存命中率 76% 还不错")
    assert len((await search(session, "76%")).conversations) == 1


# ---------- 摘要片段 ----------


def test_snippet_centers_on_match() -> None:
    text = "前面" * 50 + "关键词" + "后面" * 50
    snippet = make_snippet(text, "关键词", radius=10)
    assert "关键词" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")
    assert len(snippet) < 40


def test_snippet_collapses_whitespace() -> None:
    assert make_snippet("多行\n\n内容   有空格", "内容") == "多行 内容 有空格"


def test_snippet_without_match_falls_back_to_head() -> None:
    assert make_snippet("一段内容", "不存在") == "一段内容"


# ---------- 接口 ----------


async def test_search_endpoint(client: AsyncClient, session: AsyncSession) -> None:
    await seed(session, "示例市话题", "我住在示例市")
    await MemoryStore(session, actor="chat").create("/memories/a.md", "示例市相关记忆")
    await session.commit()

    body = (await client.get("/api/search", params={"q": "示例市"})).json()
    assert body["query"] == "示例市"
    assert len(body["conversations"]) == 1
    assert len(body["memories"]) == 1
    assert body["conversations"][0]["conversation_id"] > 0


async def test_search_endpoint_requires_q(client: AsyncClient) -> None:
    assert (await client.get("/api/search")).status_code == 422
