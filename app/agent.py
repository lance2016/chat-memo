"""把一次 agent 运行需要的东西装配到一起：provider + 工具 + system prompt。

**为什么单独一层。** 这套装配原本有三份平行实现：聊天在 `chat/router.py` 的 `_stream`
里手写、每日整理在 `jobs/consolidate.py` 里手写、评测通过整理间接用一份。三份各自
决定注册哪些工具、system prompt 开哪几段，于是出现两类静默故障：

1. **提示词和工具对不上**。`build_system_prompt` 的 `include_kb` / `include_timeline`
   是布尔参数，和实际注册的 executor 列表是两处独立的手写清单 —— 提示词里讲了某个
   工具、却没注册它，模型会反复尝试调用一个不存在的工具；反过来则是工具在但模型不知道。
2. **配置漏传**。整理那份调 `build_system_prompt(store)` 没传 settings，于是拿的是
   `.env` 启动快照，设置页里改的 `owner_name` / 自定义指令根本到不了整理任务。

所以这里把「哪些工具 + 对应的提示词段 + 什么条件下启用」收敛成**一张表**
（`TOOLKITS`），装配和提示词从同一张表推导，结构上不可能再对不上。

加一个新工具 = 在 `TOOLKITS` 里加一条，不用动 chat、jobs 或 eval 任何一处。

放在 `app/` 顶层而不是某个功能模块里，是因为它要同时用到 memory / timeline / kb /
llm 四个模块 —— 塞进其中任何一个都会造成模块间反向依赖。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.kb.tool import KbToolExecutor
from app.llm.composite import CompositeExecutor
from app.llm.factory import get_provider
from app.llm.provider import LLMProvider, ToolExecutor
from app.llm.target import ModelTarget
from app.memory.prompt import build_system_prompt
from app.memory.store import MemoryStore
from app.memory.tool import MemoryToolExecutor
from app.timeline.store import TimelineStore
from app.timeline.tool import TimelineToolExecutor

# chat = 用户对话；consolidation = 每日整理。
# 整理的输入是对话摘要，用不上知识库和时间线 —— 提示词里提了工具却不注册，模型会困惑。
Purpose = Literal["chat", "consolidation"]


@dataclass(frozen=True)
class _Deps:
    """建工具时能拿到的东西。加字段比给每个 build 函数改签名便宜。"""

    session: AsyncSession
    settings: Settings
    store: MemoryStore
    actor: str
    conversation_id: int | None


@dataclass(frozen=True)
class Toolkit:
    """一类工具：怎么建、什么时候启用、提示词里对应哪一段。

    三件事绑在一起是这张表存在的全部意义 —— 拆开就会重新长出「提示词说有、
    实际没注册」这种对不上。
    """

    name: str
    build: Callable[[_Deps], ToolExecutor]
    # 哪些用途下启用
    purposes: frozenset[str]
    # 额外的启用条件（比如知识库要挂载了 vault 才算数）
    enabled: Callable[[Settings], bool] = lambda _settings: True


TOOLKITS: tuple[Toolkit, ...] = (
    Toolkit(
        name="memory",
        build=lambda deps: MemoryToolExecutor(deps.store),
        # 记忆是主线，两种用途都要
        purposes=frozenset({"chat", "consolidation"}),
    ),
    Toolkit(
        name="timeline",
        build=lambda deps: TimelineToolExecutor(
            TimelineStore(
                deps.session,
                actor=deps.actor,
                conversation_id=deps.conversation_id,
                settings=deps.settings,
            )
        ),
        purposes=frozenset({"chat"}),
    ),
    Toolkit(
        name="kb",
        build=lambda deps: KbToolExecutor(
            deps.session, deps.settings.vault_path, deps.conversation_id
        ),
        purposes=frozenset({"chat"}),
        # vault 没挂载时整段功能关闭：工具不注册，提示词也不提
        enabled=lambda settings: bool(settings.vault_path),
    ),
)


@dataclass(frozen=True)
class AgentContext:
    """一次 agent 运行的全部输入。"""

    provider: LLMProvider
    executor: ToolExecutor
    system: str
    store: MemoryStore
    target: ModelTarget
    # 本次实际启用了哪些工具集。进日志和评测报告，方便回答「那次到底带了什么工具」
    toolkits: tuple[str, ...] = field(default_factory=tuple)


def active_toolkits(settings: Settings, purpose: Purpose) -> tuple[Toolkit, ...]:
    return tuple(
        kit
        for kit in TOOLKITS
        if purpose in kit.purposes and kit.enabled(settings)
    )


async def build_agent_context(
    session: AsyncSession,
    *,
    settings: Settings,
    target: ModelTarget | None = None,
    provider: LLMProvider | None = None,
    purpose: Purpose = "chat",
    conversation_id: int | None = None,
    actor: str = "",
) -> AgentContext:
    """装配一次 agent 运行。

    `settings` 必须是生效配置（`resolve_settings` 的结果），不是 `get_settings()`
    的启动快照 —— system prompt 里的 owner_name 和自定义指令都从它来。

    `provider` 允许直接传进来而不是由这里构造：每日整理是先拿到 provider 再装配的，
    测试也靠注入假 provider 来避免真实网络请求。传了就用传的那个。
    """
    target = target or ModelTarget.from_settings(settings)
    actor = actor or ("chat" if purpose == "chat" else "consolidation")
    store = MemoryStore(session, actor=actor, conversation_id=conversation_id)
    deps = _Deps(
        session=session,
        settings=settings,
        store=store,
        actor=actor,
        conversation_id=conversation_id,
    )

    kits = active_toolkits(settings, purpose)
    names = tuple(kit.name for kit in kits)
    executor = CompositeExecutor(*(kit.build(deps) for kit in kits))

    # 提示词开哪几段，直接由「实际注册了哪些工具」决定，不是另写一份布尔参数。
    system = await build_system_prompt(
        store,
        settings,
        include_kb="kb" in names,
        include_timeline="timeline" in names,
    )
    return AgentContext(
        provider=provider or get_provider(settings, target=target),
        executor=executor,
        system=system,
        store=store,
        target=target,
        toolkits=names,
    )
