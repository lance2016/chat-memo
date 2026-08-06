"""每日记忆整理。

聊天中的实时写入是局部的 —— 模型只看得到当前这轮对话。整理任务补上全局视角：
把当天所有对话摘要一起交给模型，让它去重、修正过期信息、把碎片提炼进 profile。
这一步比实时写更重要，也是记忆质量的主要来源。
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Conversation,
    ConversationSummary,
    MemoryVersion,
    Message,
)
from app.llm.events import Error
from app.llm.provider import LLMProvider
from app.memory.prompt import build_system_prompt
from app.memory.store import MemoryStore
from app.memory.tool import MemoryToolExecutor
from app.timeutils import local_day_bounds

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM = """你在为一份个人助手的对话记录写摘要。

只写事实，不要评价。重点保留：用户透露的关于他自己的信息、明确的偏好和决定、
正在推进的项目及进展、提到的人和组织。跳过纯技术问答的具体内容。
控制在 200 字以内。如果整段对话没有任何值得长期记住的信息，只回复"无"。"""

CONSOLIDATE_PROMPT = """下面是 {date} 这一天的对话摘要。请据此整理长期记忆。

要做的事：
1. 先 `view` 相关的现有记忆文件，别凭空猜测已有内容
2. 新信息写进对应文件；和已有记录冲突的，用 `str_replace` 改掉旧的，不要并存
3. 重复记录的合并掉
4. 把 timeline 里已经稳定下来的信息提炼进 profile/ 或 projects/
5. 最后更新 {index} 索引，确保每个记忆文件都有一行摘要

如果这天没有值得沉淀的内容，什么都不用做，直接说明即可。

---

{summaries}"""


@dataclass
class ConsolidationResult:
    date: str
    summarized_conversations: int
    # 工具调用次数（含 view 这类只读操作）
    tool_calls: int
    # 真正改动了记忆的次数。为 0 说明模型看过之后认为无需变更 —— 这是正常结果。
    memory_writes: int = 0
    skipped: bool = False
    failed_summaries: int = 0
    detail: str = ""


class Consolidator:
    def __init__(self, session: AsyncSession, provider: LLMProvider) -> None:
        self.session = session
        self.provider = provider

    async def run(self, day: dt.date | None = None) -> ConsolidationResult:
        day = day or dt.date.today()
        started = time.monotonic()
        conversations = await self._conversations_on(day)
        logger.info("整理 %s：%d 个会话", day.isoformat(), len(conversations))

        summaries: list[str] = []
        failures = 0
        for conversation in conversations:
            try:
                summary = await self._summarize(conversation)
            except Exception:
                logger.exception("生成会话摘要失败: conversation_id=%s", conversation.id)
                failures += 1
                continue
            if summary:
                summaries.append(f"## {conversation.title}\n{summary}")

        if not summaries:
            detail = (
                f"{failures} 个会话摘要生成失败，详见日志"
                if failures
                else "当天没有值得沉淀的对话"
            )
            return ConsolidationResult(
                date=day.isoformat(),
                summarized_conversations=0,
                tool_calls=0,
                skipped=True,
                failed_summaries=failures,
                detail=detail,
            )

        result = await self._apply(day, summaries, len(summaries), started)
        result.failed_summaries = failures
        return result

    async def _conversations_on(self, day: dt.date) -> list[Conversation]:
        start, end = local_day_bounds(day)
        stmt = (
            select(Conversation)
            .join(Message, Message.conversation_id == Conversation.id)
            .where(Message.created_at >= start, Message.created_at < end)
            .distinct()
            .order_by(Conversation.id)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def _summarize(self, conversation: Conversation) -> str:
        """增量摘要：只处理上次水位线之后的新消息。"""
        watermark = await self.session.scalar(
            select(ConversationSummary.up_to_message_id)
            .where(ConversationSummary.conversation_id == conversation.id)
            .order_by(ConversationSummary.id.desc())
            .limit(1)
        )

        stmt = select(Message).where(Message.conversation_id == conversation.id)
        if watermark is not None:
            stmt = stmt.where(Message.id > watermark)
        messages = list((await self.session.execute(stmt.order_by(Message.id))).scalars())
        if not messages:
            return ""

        transcript = _render_transcript(messages)
        if not transcript.strip():
            return ""

        # 失败交给调用方计数上报，这里不吞异常。
        summary = await self.provider.complete(
            system=SUMMARY_SYSTEM, prompt=transcript, max_tokens=4000
        )

        summary = summary.strip()
        if not summary or summary == "无":
            return ""

        self.session.add(
            ConversationSummary(
                conversation_id=conversation.id,
                summary=summary,
                up_to_message_id=messages[-1].id,
            )
        )
        await self.session.commit()
        return summary

    async def _apply(
        self, day: dt.date, summaries: list[str], conversation_count: int,
        started: float = 0.0,
    ) -> ConsolidationResult:
        from app.memory.paths import INDEX_PATH

        store = MemoryStore(self.session, actor="consolidation")
        executor = MemoryToolExecutor(store)
        # 整理的输入是对话摘要，用不上知识库 —— 不注册 kb 工具，提示词里也别提它
        system = await build_system_prompt(store, include_kb=False)
        prompt = CONSOLIDATE_PROMPT.format(
            date=day.isoformat(), index=INDEX_PATH, summaries="\n\n".join(summaries)
        )

        # 用版本表的水位线数「真正写了几次」，比数工具调用准 —— view 不算改动。
        before = await self.session.scalar(select(func.max(MemoryVersion.id))) or 0

        tool_calls = 0
        detail = ""
        async for event in self.provider.run(
            system=system,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            executor=executor,
        ):
            if event.type == "tool_use":
                tool_calls += 1
            elif isinstance(event, Error):
                detail = event.message
                logger.error("记忆整理出错: %s", event.message)

        await self.session.commit()
        writes_stmt = await self.session.scalar(
            select(func.count(MemoryVersion.id)).where(MemoryVersion.id > before)
        )
        writes = writes_stmt or 0
        logger.info(
            "整理完成：%d 摘要 · %d 工具调用 · %d 次写入 · %.1fs",
            conversation_count, tool_calls, writes, time.monotonic() - started,
        )
        return ConsolidationResult(
            date=day.isoformat(),
            summarized_conversations=conversation_count,
            tool_calls=tool_calls,
            memory_writes=writes,
            detail=detail,
        )


def _render_transcript(messages: list[Message]) -> str:
    """把 content block 数组压成纯文本。

    thinking 和 tool_use 对摘要没有价值，丢掉；只保留双方说的话。
    """
    lines: list[str] = []
    for message in messages:
        texts = [
            block.get("text", "")
            for block in message.content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        body = "\n".join(t for t in texts if t.strip())
        if not body:
            continue
        speaker = "用户" if message.role == "user" else "助手"
        lines.append(f"{speaker}：{body}")
    return "\n\n".join(lines)
