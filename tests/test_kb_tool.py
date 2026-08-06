from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import KbRead
from app.kb.tool import KB_TOOL_NAMES, KbToolExecutor
from app.llm.composite import CompositeExecutor
from app.memory.prompt import build_system_prompt
from app.memory.store import MemoryStore
from app.memory.tool import MemoryToolExecutor


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("关于 uv 的笔记\n链接到 [[other]]\n")
    (root / "other.md").write_text("另一篇\n")
    return root


@pytest.fixture
def executor(session: AsyncSession, vault: Path) -> KbToolExecutor:
    return KbToolExecutor(session, str(vault), conversation_id=7)


async def _kb_reads(session: AsyncSession) -> list[KbRead]:
    return list((await session.execute(select(KbRead))).scalars())


async def test_search_dispatch_and_tracking(
    executor: KbToolExecutor, session: AsyncSession
) -> None:
    result, is_error = await executor.execute("kb_search", {"query": "uv"})
    assert not is_error
    assert "note.md" in result

    (row,) = await _kb_reads(session)
    assert (row.command, row.target, row.found, row.conversation_id) == (
        "search", "uv", True, 7,
    )


async def test_search_miss_records_found_false(
    executor: KbToolExecutor, session: AsyncSession
) -> None:
    result, is_error = await executor.execute("kb_search", {"query": "不存在的词"})
    assert not is_error  # 没搜到不是错误，是有效信息
    assert "没有搜到" in result

    (row,) = await _kb_reads(session)
    assert row.found is False


async def test_read_and_list_and_backlinks(executor: KbToolExecutor) -> None:
    result, is_error = await executor.execute("kb_read", {"path": "note.md"})
    assert not is_error
    assert "1\t关于 uv 的笔记" in result

    result, is_error = await executor.execute("kb_list", {})
    assert not is_error
    assert "note.md" in result

    result, is_error = await executor.execute("kb_backlinks", {"path": "other.md"})
    assert not is_error
    assert "note.md" in result


async def test_errors_are_returned_not_raised(
    executor: KbToolExecutor, session: AsyncSession
) -> None:
    result, is_error = await executor.execute("kb_read", {"path": "nope.md"})
    assert is_error
    assert "不存在" in result
    (row,) = await _kb_reads(session)
    assert (row.command, row.found) == ("read", False)

    result, is_error = await executor.execute("kb_read", {})
    assert is_error  # 缺参数

    result, is_error = await executor.execute("kb_search", {"query": "x", "limit": "三"})
    assert is_error

    result, is_error = await executor.execute("kb_read", {"path": "../etc/passwd"})
    assert is_error


async def test_unknown_tool_name(executor: KbToolExecutor) -> None:
    result, is_error = await executor.execute("kb_delete", {"path": "note.md"})
    assert is_error


# ---------- CompositeExecutor ----------


async def test_composite_routes_memory_and_kb(
    session: AsyncSession, vault: Path
) -> None:
    memory_executor = MemoryToolExecutor(MemoryStore(session, actor="test"))
    kb_executor = KbToolExecutor(session, str(vault))
    composite = CompositeExecutor(memory_executor, kb_executor)

    # 定义拼接：memory 1 个 + kb 4 个
    assert len(composite.anthropic_definitions) == 1 + len(KB_TOOL_NAMES)
    assert len(composite.openai_definitions) == 1 + len(KB_TOOL_NAMES)

    result, is_error = await composite.execute(
        "memory",
        {"command": "create", "path": "/memories/a.md", "file_text": "内容"},
    )
    assert not is_error

    result, is_error = await composite.execute("kb_search", {"query": "uv"})
    assert not is_error
    assert "note.md" in result

    result, is_error = await composite.execute("no_such_tool", {})
    assert is_error


# ---------- system prompt 开关 ----------


async def test_prompt_includes_kb_only_when_vault_configured(
    session: AsyncSession,
) -> None:
    store = MemoryStore(session, actor="test")

    with_vault = await build_system_prompt(store, Settings(vault_path="/vault"))
    assert "# 知识库" in with_vault
    assert "kb_search" in with_vault

    without_vault = await build_system_prompt(store, Settings(vault_path=""))
    assert "# 知识库" not in without_vault

    # 每日整理不挂 kb 工具，提示词里也不该提
    consolidation = await build_system_prompt(
        store, Settings(vault_path="/vault"), include_kb=False
    )
    assert "# 知识库" not in consolidation
