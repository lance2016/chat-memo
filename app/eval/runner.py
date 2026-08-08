"""重放：把一条样本喂给**真正的** `Consolidator`，收集它做了什么。

这个模块只有一条设计原则，但它决定了整套评测有没有意义：

**评的必须是生产代码路径。** 每条样本走的是 `Consolidator.run()`，和凌晨四点跑的
是同一个函数、同一套提示词、同一个 agent loop。为评测另写一份简化版整理逻辑，
评出来的分数是那份简化版的分数，和线上跑的东西没有关系 —— 这是评测最容易出的错，
而且出了之后没有任何症状。

代价是每条样本都要一个能跑 `Consolidator` 的数据库。解法是**每条样本一个全新的
内存 SQLite**：和 `tests/conftest.py` 同一套路子，跑完即弃。三个好处 ——
样本之间完全隔离（上一条写的记忆不会污染下一条）、起始状态严格等于
`memory_before`（可复现的前提）、评测永远碰不到生产库。
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Conversation, Memory, Message
from app.eval.dataset import EvalCase
from app.jobs.consolidate import ConsolidationResult, Consolidator
from app.llm.provider import LLMProvider
from app.memory.audit import IndexAudit, audit_index
from app.obs import bind

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryDiff:
    """整理前后的记忆快照差异。判分只看这个，不看模型说了什么。"""

    created: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()

    @property
    def changed(self) -> tuple[str, ...]:
        return tuple(sorted(self.created + self.modified + self.deleted))

    @property
    def is_empty(self) -> bool:
        return not self.changed

    def render(self) -> str:
        parts = []
        if self.created:
            parts.append(f"新建 {len(self.created)}")
        if self.modified:
            parts.append(f"修改 {len(self.modified)}")
        if self.deleted:
            parts.append(f"删除 {len(self.deleted)}")
        return "、".join(parts) or "无改动"


@dataclass
class CaseRun:
    """一条样本跑一次的全部产出。判分（metrics/judge）在别处，这里只负责如实记录。"""

    case_id: str
    memory_after: dict[str, str] = field(default_factory=dict)
    diff: MemoryDiff = field(default_factory=MemoryDiff)
    audit: IndexAudit = field(default_factory=IndexAudit)
    # 第 1 层过程指标直接取自生产代码的返回值，不另算一份
    result: ConsolidationResult | None = None
    transcript: str = ""
    seconds: float = 0.0
    # 整理本身崩了。和「整理跑完但质量差」是两回事，必须分开看
    crashed: bool = False
    detail: str = ""


async def run_case(
    case: EvalCase, provider: LLMProvider, *, sequence: int = 0
) -> CaseRun:
    """在一个一次性数据库里重放一条样本。

    `sequence` 只在同一条样本重复跑（测噪声）时用来区分，进 trace 元数据。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    started = time.monotonic()
    run = CaseRun(case_id=case.id)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            day = await _seed(session, case)
            run.transcript = _transcript(case)
            # 标 `run_kind` 而不是 `purpose`：`Consolidator.run` 内部会把 purpose
            # 设成 consolidate，套在外面的 purpose 会被它盖掉。用一个它不碰的字段，
            # Phoenix 里才分得开评测和真实整理 —— 混在一起会把生产的成本统计和
            # 延迟分布搅乱。
            with bind(
                run_kind="eval",
                eval_case=case.id,
                session_id=f"{case.id}#{sequence}",
            ):
                try:
                    run.result = await Consolidator(session, provider).run(day)
                except Exception as exc:
                    # 不 re-raise：一条样本崩了不该让整轮评测停下来，但必须记下来 ——
                    # crashed 的样本在报告里单独一列，绝不能当成 0 分混进平均值。
                    logger.exception("样本执行失败: case=%s", case.id)
                    run.crashed = True
                    run.detail = f"{type(exc).__name__}: {exc}"

            _flag_dropped_input(run)
            run.memory_after = await _snapshot(session)
            run.audit = audit_index(
                list((await session.execute(select(Memory))).scalars())
            )
    finally:
        await engine.dispose()

    run.diff = diff_memory(case.memory_before, run.memory_after)
    run.seconds = time.monotonic() - started
    return run


def _flag_dropped_input(run: CaseRun) -> None:
    """摘要失败 = 这条样本的输入被吃掉了一部分，结果不能当质量信号。

    生产代码在这里的行为是对的：一个会话摘要失败就跳过它，别让整天的整理泡汤
    （`Consolidator._run` 里的 `failed_summaries`）。但对评测来说这是**最危险的
    一种静默失败** —— 模型没看到那段对话，自然什么都没记，分数看起来只是「召回低」，
    而真正的原因是输入根本没送到。更糟的是 `no_op` 样本：输入全丢了反而会判成满分。

    所以评测这一层把它升级成「本条不可用」，宁可少一个数据点也不要一个假的。
    """
    result = run.result
    if result is None or run.crashed or not result.failed_summaries:
        return
    run.crashed = True
    run.detail = (
        f"{result.failed_summaries} 个会话摘要失败，输入不完整，本条不参与打分"
    )
    logger.warning("样本输入不完整: case=%s %s", run.case_id, run.detail)


def diff_memory(before: dict[str, str], after: dict[str, str]) -> MemoryDiff:
    """比较两份快照。内容一模一样不算修改 —— 模型「改了但改回原样」不该计入活动量。"""
    before_paths, after_paths = set(before), set(after)
    return MemoryDiff(
        created=tuple(sorted(after_paths - before_paths)),
        modified=tuple(
            sorted(p for p in before_paths & after_paths if before[p] != after[p])
        ),
        deleted=tuple(sorted(before_paths - after_paths)),
    )


async def _seed(session: AsyncSession, case: EvalCase) -> dt.date:
    """把样本写进空库，返回该整理哪一天。

    记忆直接插 `Memory` 行，不走 `MemoryStore` —— store 会顺带写 `memory_versions`，
    而那张表正是 `_apply` 用来数「这次写了几笔」的水位线。种子数据不该被算成模型的产出。
    """
    for path, content in case.memory_before.items():
        session.add(Memory(path=path, content=content))

    day = dt.date.fromisoformat(case.date) if case.date else dt.date.today()
    # 消息时间戳必须落在这一天的本地窗口里，否则 `_conversations_on` 查不到它们。
    # 用正午而不是 00:00：时区偏移不管往哪边偏都不会把这天挤出去。
    when = dt.datetime.combine(day, dt.time(12, 0)).astimezone()

    for item in case.conversations:
        conversation = Conversation(title=item.title, created_at=when, updated_at=when)
        session.add(conversation)
        await session.flush()
        for message in item.messages:
            session.add(
                Message(
                    conversation_id=conversation.id,
                    role=message.role,
                    content=[{"type": "text", "text": message.text}],
                    created_at=when,
                )
            )
    await session.commit()
    return day


async def _snapshot(session: AsyncSession) -> dict[str, str]:
    rows = (await session.execute(select(Memory))).scalars()
    return {row.path: row.content for row in rows}


def _transcript(case: EvalCase) -> str:
    """判「有没有编造」时给裁判当依据的对话原文。

    和 `consolidate._render_transcript` 是同一个格式，但读的是样本而不是数据库行 ——
    裁判看到的对话必须和整理看到的一模一样，否则「记忆里这句话有没有出处」判不准。
    """
    blocks = []
    for item in case.conversations:
        lines = [f"## {item.title}"]
        lines += [
            f"{'用户' if m.role == 'user' else '助手'}：{m.text}"
            for m in item.messages
            if m.text.strip()
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
