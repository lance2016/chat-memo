"""用户自定义指令：手写的、只有用户能改的那部分 system prompt。

和记忆的边界是这套测试的重点 —— 它必须进 prompt，但不能进 /memories，
否则每日整理会把它当普通记忆去重掉。
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.memory.prompt import build_system_prompt
from app.memory.store import MemoryStore
from app.settings_store import SettingError, apply, describe, validate


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------- 组装 ----------


async def test_instructions_appear_in_prompt(store: MemoryStore) -> None:
    settings = Settings(owner_name="阿明", custom_instructions="回答控制在三句话以内")
    prompt = await build_system_prompt(store, settings)

    assert "回答控制在三句话以内" in prompt
    assert "阿明的额外指令" in prompt


async def test_section_absent_when_empty(store: MemoryStore) -> None:
    """留空时整段不出现 —— 不能给模型一个空标题让它猜。"""
    prompt = await build_system_prompt(store, Settings(custom_instructions=""))
    assert "额外指令" not in prompt


async def test_whitespace_only_counts_as_empty(store: MemoryStore) -> None:
    prompt = await build_system_prompt(store, Settings(custom_instructions="  \n  "))
    assert "额外指令" not in prompt


async def test_instructions_go_last(store: MemoryStore) -> None:
    """system prompt 的结尾是指令遵循最强的位置，这段权威性最高，必须在最后。"""
    settings = Settings(custom_instructions="用英文回答")
    prompt = await build_system_prompt(store, settings)

    assert prompt.index("用英文回答") > prompt.index("</memory_index>")


async def test_current_request_outranks_persistent_context(
    store: MemoryStore,
) -> None:
    """长期偏好和记忆不能压过用户在当前消息里的明确要求。"""
    prompt = await build_system_prompt(store, Settings())
    assert "当前消息中的明确要求优先于长期偏好" in prompt
    assert "长期记忆只提供背景事实" in prompt


async def test_instructions_declared_as_overriding(store: MemoryStore) -> None:
    """必须明说优先级高于默认人格，否则和上面的「都用中文回答」冲突时行为随机。"""
    prompt = await build_system_prompt(
        store, Settings(custom_instructions="用英文回答")
    )
    assert "优先级高于上面的所有默认设定" in prompt


async def test_model_told_not_to_edit_it(store: MemoryStore) -> None:
    """模型有 memory 工具，不说清楚它会试图把这段「整理」进记忆。"""
    prompt = await build_system_prompt(store, Settings(custom_instructions="X"))
    assert "不能也不需要用 memory 工具修改它" in prompt


async def test_not_stored_as_a_memory(
    store: MemoryStore, session: AsyncSession
) -> None:
    """自定义指令走设置表，不该在 /memories 里留下任何文件。"""
    await build_system_prompt(store, Settings(custom_instructions="别用 pip"))
    assert await store.list_all() == []


# ---------- 校验 ----------


def test_length_capped() -> None:
    """每轮都进 prompt，没有上限的话用户能往里塞一本书。"""
    with pytest.raises(SettingError, match="最多 4000"):
        validate("custom_instructions", "啊" * 4001, Settings())


def test_empty_allowed() -> None:
    assert validate("custom_instructions", "", Settings()) == ""


def test_declared_as_multiline() -> None:
    """kind=text 是给前端的渲染提示：多行文本框，不是单行输入。"""
    fields = {f["key"]: f for f in describe(Settings(), {})["fields"]}
    assert fields["custom_instructions"]["kind"] == "text"
    assert fields["custom_instructions"]["group"] == "prompt"
    assert fields["owner_name"]["group"] == "prompt"


# ---------- 端到端 ----------


async def test_takes_effect_without_restart(
    client: AsyncClient, session: AsyncSession
) -> None:
    """改完立刻生效是这个功能的价值所在 —— 调 prompt 不该要重启容器。"""
    before = (await client.get("/api/debug/prompt")).json()["system"]
    assert "只用 uv，不要 pip" not in before

    await apply(session, {"custom_instructions": "只用 uv，不要 pip"}, Settings())
    await session.commit()

    after = (await client.get("/api/debug/prompt")).json()["system"]
    assert "只用 uv，不要 pip" in after
    assert len(after) > len(before)
