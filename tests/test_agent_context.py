"""共用的 agent 装配层。

这些用例钉住的是**「工具、提示词、配置」三者不许各走各的**。原来这套装配在聊天、
每日整理、评测里各有一份手写实现，两类静默故障就是从那儿来的：提示词讲了某个工具
却没注册它，以及整理任务拿不到设置页改的配置。
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import TOOLKITS, active_toolkits, build_agent_context
from app.config import Settings
from app.llm.target import ModelTarget


def _settings(**overrides) -> Settings:
    """显式给出 vault_path 的默认值。

    `Settings()` 会读开发机上的 `.env`，配了 `VAULT_PATH` 的机器上「没挂知识库」
    这个前提就不成立了 —— 用例会在一台机器上过、另一台上挂。
    """
    return Settings(
        provider="deepseek",
        deepseek_api_key="t",
        **{"vault_path": "", **overrides},
    )


# ---------- 工具集与提示词一致 ----------


async def test_prompt_sections_follow_the_registered_tools(session: AsyncSession) -> None:
    """提示词开哪几段由「实际注册了哪些工具」决定。

    这两处原本是两份独立的手写清单，对不上的后果是模型反复调用一个不存在的工具，
    或者手里有工具却不知道。
    """
    context = await build_agent_context(
        session, settings=_settings(), purpose="chat", conversation_id=1
    )

    assert "timeline" in context.toolkits
    assert "# 时间线" in context.system
    # 没挂 vault，kb 工具和它的提示词段应当一起消失
    assert "kb" not in context.toolkits
    assert "# 知识库" not in context.system


async def test_kb_is_all_or_nothing(session: AsyncSession, tmp_path) -> None:
    """挂了 vault 就工具和提示词一起出现；两者永远同进同退。"""
    context = await build_agent_context(
        session,
        settings=_settings(vault_path=str(tmp_path)),
        purpose="chat",
        conversation_id=1,
    )

    assert "kb" in context.toolkits
    assert "# 知识库" in context.system


async def test_consolidation_gets_memory_only(session: AsyncSession, tmp_path) -> None:
    """整理的输入是对话摘要，用不上知识库和时间线。

    即使挂了 vault 也不该给它 kb 工具 —— 提示词里提了工具却不注册，模型会困惑。
    """
    context = await build_agent_context(
        session,
        settings=_settings(vault_path=str(tmp_path)),
        purpose="consolidation",
    )

    assert context.toolkits == ("memory",)
    assert "# 知识库" not in context.system
    assert "# 时间线" not in context.system


async def test_every_toolkit_declares_at_least_one_purpose() -> None:
    """注册表里一条没有 purpose 的工具永远不会被启用，而且不会报错。"""
    for kit in TOOLKITS:
        assert kit.purposes, kit.name


def test_toolkit_names_are_unique() -> None:
    names = [kit.name for kit in TOOLKITS]
    assert len(names) == len(set(names))


# ---------- 配置必须是生效值 ----------


async def test_system_prompt_uses_the_settings_passed_in(session: AsyncSession) -> None:
    """整理任务原来调 `build_system_prompt(store)` 没传 settings，于是拿的是 .env
    启动快照 —— 设置页里改的称呼和自定义指令根本到不了整理任务，而且毫无症状。
    """
    settings = _settings(owner_name="Lance", custom_instructions="回答一律用中文")

    context = await build_agent_context(
        session, settings=settings, purpose="consolidation"
    )

    assert "Lance" in context.system
    assert "回答一律用中文" in context.system


async def test_executor_routes_to_the_registered_tools(session: AsyncSession) -> None:
    context = await build_agent_context(
        session, settings=_settings(), purpose="chat", conversation_id=1
    )
    names = {d["name"] for d in context.executor.anthropic_definitions}

    assert "memory" in names
    assert any(name.startswith("timeline_") for name in names)


# ---------- provider 注入 ----------


async def test_an_injected_provider_is_used_as_is(session: AsyncSession) -> None:
    """每日整理先拿到 provider 再装配；测试也靠注入假 provider 避免真实网络请求。"""
    from app.config import Settings as S
    from app.llm.anthropic_provider import AnthropicProvider
    from tests.fakes import FakeAnthropic

    injected = AnthropicProvider(
        settings=S(anthropic_api_key="test"), client=FakeAnthropic([])
    )
    context = await build_agent_context(
        session, settings=_settings(), provider=injected, purpose="consolidation"
    )

    assert context.provider is injected


async def test_target_defaults_to_the_legacy_settings(session: AsyncSession) -> None:
    context = await build_agent_context(
        session, settings=_settings(deepseek_model="flash"), purpose="consolidation"
    )

    assert context.target.model_id == "flash"
    assert context.provider.model_name == "flash"


async def test_an_explicit_target_wins(session: AsyncSession) -> None:
    target = ModelTarget(
        protocol="openai_compatible",
        model_id="qwen-max",
        display_name="Qwen",
        base_url="https://example.invalid/v1",
        api_key="k",
    )
    context = await build_agent_context(
        session, settings=_settings(), target=target, purpose="consolidation"
    )

    assert context.provider.model_name == "qwen-max"


@pytest.mark.parametrize("purpose", ["chat", "consolidation"])
async def test_memory_is_always_on(purpose: str) -> None:
    """记忆是这个产品的主线，任何用途下都不该缺。"""
    assert "memory" in {kit.name for kit in active_toolkits(_settings(), purpose)}


# ---------- 给人看的目录 ----------


async def test_catalog_lists_disabled_toolkits_too(session: AsyncSession) -> None:
    """停用的工具也要出现在目录里，并说明怎么开启。

    让知识库在没挂 vault 时凭空消失，人会以为这个功能根本不存在。
    """
    from app.agent import describe_toolkits

    described = describe_toolkits(session, _settings(), purpose="chat")
    by_name = {kit.name: enabled for kit, enabled, _ in described}

    assert by_name["kb"] is False
    assert by_name["memory"] is True


async def test_catalog_definitions_come_from_the_executor(session: AsyncSession) -> None:
    """schema 从 executor 上取 —— 它是工具定义的唯一事实来源，也正是聊天时
    真正交给模型的那份。另存一份就等于又造了一张会漂移的手写清单。
    """
    from app.agent import describe_toolkits

    described = describe_toolkits(session, _settings(), purpose="chat")
    timeline = next(exe for kit, _, exe in described if kit.name == "timeline")
    names = {d["function"]["name"] for d in timeline.openai_definitions}

    assert names == {"timeline_list", "timeline_create", "timeline_update"}


def test_every_toolkit_has_a_label() -> None:
    """目录靠 label 分组显示；漏填会让那一类工具挂在一个空标题下。"""
    for kit in TOOLKITS:
        assert kit.label, kit.name


def test_a_conditionally_enabled_toolkit_explains_how_to_turn_it_on() -> None:
    """`enabled` 不恒真的工具必须给出开启方法，否则界面上只能显示「未启用」四个字。"""
    from app.config import Settings as S

    for kit in TOOLKITS:
        always_on = kit.enabled(S(vault_path="")) and kit.enabled(S(vault_path="/x"))
        if not always_on:
            assert kit.disabled_hint, kit.name
