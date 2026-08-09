"""技能和 agent 装配的接缝：提示词第 0 层、启停、工具白名单。

这几条钉的都是**静默故障**：出错时不报错，只是模型悄悄看不见或看得见某个技能。
"""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import active_toolkits, build_agent_context
from app.config import Settings
from app.memory.prompt import build_system_prompt
from app.memory.store import MemoryStore
from app.skills.service import active_entries, load_catalog, set_enabled
from app.skills.store import SkillEntry, SkillStore
from app.skills.tool import SkillToolExecutor
from tests.test_skills_store import write_skill


def settings_with(root: Path, **overrides) -> Settings:
    # 显式构造，不读开发机的 .env —— CI 里没有那个文件
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        skills_path=str(root),
        **overrides,
    )


# ---- system prompt 的第 0 层 ----


async def test_prompt_lists_name_and_description_only(store: MemoryStore):
    skills = (
        SkillEntry(name="pdf", description="处理 PDF 时使用。"),
        SkillEntry(name="excel", description="处理表格时使用。"),
    )

    prompt = await build_system_prompt(store, Settings(), skills=skills)

    assert "- pdf — 处理 PDF 时使用。" in prompt
    assert "- excel — 处理表格时使用。" in prompt
    assert "skill_read" in prompt


async def test_prompt_omits_section_without_skills(store: MemoryStore):
    """一个技能都没有时不提技能，否则模型会去猜一个名字调用 skill_read。"""
    prompt = await build_system_prompt(store, Settings())

    assert "<available_skills>" not in prompt


async def test_prompt_has_no_volatile_content(store: MemoryStore):
    """system prompt 是 prompt cache 的稳定前缀，技能段不能带路径或时间戳。"""
    skills = (SkillEntry(name="pdf", description="处理 PDF。", size_bytes=1234),)

    prompt = await build_system_prompt(store, Settings(), skills=skills)

    assert "1234" not in prompt
    assert "/skills" not in prompt


# ---- 启用状态 ----


async def test_disabled_skill_disappears_from_active(
    session: AsyncSession, tmp_path: Path
):
    write_skill(tmp_path, "alpha")
    write_skill(tmp_path, "beta")
    settings = settings_with(tmp_path)

    await set_enabled(session, "alpha", False)

    assert [e.name for e in await active_entries(session, settings)] == ["beta"]
    # 停用不是删除：界面上还要能看到它并重新打开
    assert [v.entry.name for v in await load_catalog(session, settings)] == [
        "alpha", "beta"
    ]


async def test_broken_skill_never_reaches_the_model(
    session: AsyncSession, tmp_path: Path
):
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "SKILL.md").write_text("没有 frontmatter", encoding="utf-8")
    settings = settings_with(tmp_path)

    assert await active_entries(session, settings) == ()
    assert len(await load_catalog(session, settings)) == 1


async def test_manually_copied_skill_is_enabled_without_a_row(
    session: AsyncSession, tmp_path: Path
):
    """磁盘是事实来源：拷进来就能用，不需要经过安装接口。"""
    write_skill(tmp_path, "alpha")

    assert [e.name for e in await active_entries(session, settings_with(tmp_path))] == [
        "alpha"
    ]


# ---- 工具白名单 ----


async def test_tool_refuses_skills_outside_the_allowed_set(tmp_path: Path):
    """停用的技能名可能还留在历史消息里；不在这里拦，「停用」就只是装饰。"""
    write_skill(tmp_path, "alpha")
    executor = SkillToolExecutor(SkillStore(tmp_path), frozenset())

    result, is_error = await executor.execute("skill_read", {"name": "alpha"})

    assert is_error
    assert "没有名为" in result


async def test_tool_reads_manifest_and_lists_files(tmp_path: Path):
    write_skill(tmp_path, "alpha", body="第一步。", files={"refs/a.md": "# A"})
    executor = SkillToolExecutor(SkillStore(tmp_path), frozenset({"alpha"}))

    result, is_error = await executor.execute("skill_read", {"name": "alpha"})

    assert not is_error
    assert "第一步。" in result
    assert "refs/a.md" in result


async def test_tool_path_traversal_is_an_error_not_an_exception(tmp_path: Path):
    """工具的失败必须变成 is_error 的结果文本，让模型自己纠正。"""
    write_skill(tmp_path, "alpha")
    executor = SkillToolExecutor(SkillStore(tmp_path), frozenset({"alpha"}))

    result, is_error = await executor.execute(
        "skill_file", {"name": "alpha", "path": "../../etc/passwd"}
    )

    assert is_error
    assert "越出" in result or "path" in result


# ---- 装配 ----


def test_toolkit_off_when_disabled(tmp_path: Path):
    off = settings_with(tmp_path, skills_enabled=False)
    assert "skills" not in [kit.name for kit in active_toolkits(off, "chat")]

    on = settings_with(tmp_path)
    assert "skills" in [kit.name for kit in active_toolkits(on, "chat")]


def test_toolkit_absent_from_consolidation(tmp_path: Path):
    """整理写的是长期记忆，被技能带偏的代价是持久的。"""
    settings = settings_with(tmp_path)
    assert "skills" not in [
        kit.name for kit in active_toolkits(settings, "consolidation")
    ]


@pytest.mark.parametrize("enabled", [True, False])
async def test_prompt_and_tool_see_the_same_skills(
    session: AsyncSession, tmp_path: Path, enabled: bool
):
    """两边分别查一次的话，中途装一个技能就会出现「提示词里有、工具说没有」。"""
    write_skill(tmp_path, "alpha")
    settings = settings_with(tmp_path)
    await set_enabled(session, "alpha", enabled)

    context = await build_agent_context(
        session, settings=settings, provider=_FakeProvider()
    )

    in_prompt = "- alpha —" in context.system
    result, is_error = await context.executor.execute("skill_read", {"name": "alpha"})
    assert in_prompt is enabled
    assert (not is_error) is enabled


class _FakeProvider:
    """只为跳过真实 provider 构造（会要 API key）。装配本身不调用它。"""

    model_name = "fake"

    def run(self, **_kwargs):
        raise NotImplementedError

    async def complete(self, **_kwargs) -> str:
        raise NotImplementedError
