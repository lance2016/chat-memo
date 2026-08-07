"""每日整理：沉淀记忆 + 写这一天的回顾。

聊天中的实时写入是局部的 —— 模型只看得到当前这轮对话。整理任务补上全局视角：
把当天所有对话摘要一起交给模型，让它去重、修正过期信息、把碎片提炼进 profile。
这一步比实时写更重要，也是记忆质量的主要来源。

同一批摘要还喂给第二件事：每日回顾。摘要那一次调用同时产出两份文本
（见 prompts.SUMMARY_SYSTEM），memory 那份走记忆链路，recap 那份汇总成
「今日一句 + 收获 + 悬而未决」。回顾失败不影响记忆 —— 记忆是主线。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Conversation,
    ConversationSummary,
    DailyDigest,
    MemoryVersion,
    Message,
    OpenLoop,
)
from app.jobs.prompts import (
    CONSOLIDATE_PROMPT,
    DIGEST_PROMPT,
    DIGEST_SYSTEM,
    SUMMARY_SYSTEM,
)
from app.llm.events import Error
from app.llm.provider import LLMProvider
from app.memory.prompt import build_system_prompt
from app.memory.store import MemoryStore
from app.memory.tool import MemoryToolExecutor
from app.timeutils import local_day_bounds

logger = logging.getLogger(__name__)


@dataclass
class _ConversationTake:
    """一次摘要的两份产出。"""

    conversation_id: int
    title: str
    memory: str
    recap: str
    open_loops: list[str] = field(default_factory=list)


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
    # 这一天的回顾。digest_failed 时 headline 为空，但记忆整理的结果依然有效。
    headline: str = ""
    new_loops: int = 0
    closed_loops: int = 0
    digest_failed: bool = False


class Consolidator:
    def __init__(self, session: AsyncSession, provider: LLMProvider) -> None:
        self.session = session
        self.provider = provider

    async def run(self, day: dt.date | None = None) -> ConsolidationResult:
        day = day or dt.date.today()
        started = time.monotonic()
        conversations = await self._conversations_on(day)
        logger.info("整理 %s：%d 个会话", day.isoformat(), len(conversations))

        takes: list[_ConversationTake] = []
        failures = 0
        for conversation in conversations:
            try:
                take = await self._summarize(conversation)
            except Exception:
                logger.exception("生成会话摘要失败: conversation_id=%s", conversation.id)
                failures += 1
                continue
            if take is not None:
                takes.append(take)

        summaries = [f"## {t.title}\n{t.memory}" for t in takes if t.memory]

        if not summaries and not any(t.recap for t in takes):
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

        if summaries:
            result = await self._apply(day, summaries, len(summaries), started)
        else:
            # 有 recap 没 memory：这天聊的都是不值得进记忆的技术活。
            # 记忆整理跳过，但回顾照写 —— 那恰恰是回顾最该记的东西。
            result = ConsolidationResult(
                date=day.isoformat(), summarized_conversations=len(takes), tool_calls=0
            )
        result.failed_summaries = failures

        try:
            await self._digest(day, takes, result)
        except Exception:
            logger.exception("生成每日回顾失败: day=%s", day.isoformat())
            # 回滚回顾这一步的半成品。记忆整理已经 commit 过了，不受影响。
            await self.session.rollback()
            result.digest_failed = True
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

    async def _summarize(self, conversation: Conversation) -> _ConversationTake | None:
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
            return None

        transcript = _render_transcript(messages)
        if not transcript.strip():
            return None

        # 失败交给调用方计数上报，这里不吞异常。
        raw = await self.provider.complete(
            system=SUMMARY_SYSTEM, prompt=transcript, max_tokens=4000
        )

        take = _parse_summary(raw, conversation)
        if take is None:
            return None

        self.session.add(
            ConversationSummary(
                conversation_id=conversation.id,
                summary=take.memory,
                recap=take.recap or None,
                up_to_message_id=messages[-1].id,
            )
        )
        await self.session.commit()
        return take

    async def _apply(
        self, day: dt.date, summaries: list[str], conversation_count: int,
        started: float = 0.0,
    ) -> ConsolidationResult:
        from app.memory.paths import INDEX_PATH

        store = MemoryStore(self.session, actor="consolidation")
        executor = MemoryToolExecutor(store)
        # 整理的输入是对话摘要，用不上知识库 —— 不注册 kb 工具，提示词里也别提它
        system = await build_system_prompt(store, include_kb=False, include_timeline=False)
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


    async def _digest(
        self, day: dt.date, takes: list[_ConversationTake], result: ConsolidationResult
    ) -> None:
        """写这一天的「今日一句 + 收获」，顺带结算悬而未决。

        闭环判断放在这一次调用里，因为只有这里同时看得到「今天做了什么」和
        「之前挂着什么」—— 拆成两次调用后一半的上下文就丢了。
        """
        recaps = [f"## {t.title}\n{t.recap}" for t in takes if t.recap]
        if not recaps:
            logger.info("回顾跳过 %s：没有可用的 recap", day.isoformat())
            return

        pending = list(
            (
                await self.session.execute(
                    select(OpenLoop)
                    .where(OpenLoop.status == "open", OpenLoop.opened_on < day)
                    .order_by(OpenLoop.opened_on)
                )
            ).scalars()
        )
        candidates = [loop for t in takes for loop in t.open_loops]

        prompt = DIGEST_PROMPT.format(
            date=day.isoformat(),
            recaps="\n\n".join(recaps),
            new_loops="\n".join(f"- {c}" for c in candidates) or "（无）",
            open_loops="\n".join(
                f"- id={loop.id}（{loop.opened_on.isoformat()} 起）{loop.text}"
                for loop in pending
            )
            or "（无）",
        )
        raw = await self.provider.complete(
            system=DIGEST_SYSTEM, prompt=prompt, max_tokens=4000
        )

        data = _parse_json_object(raw)
        if data is None:
            raise ValueError(f"回顾输出不是 JSON: {raw[:200]!r}")

        headline = str(data.get("headline") or "").strip()
        highlights = [
            str(item).strip()
            for item in data.get("highlights") or []
            if str(item).strip()
        ]
        if not headline:
            raise ValueError("回顾输出缺少 headline")

        await self._upsert_digest(day, headline, highlights)

        pending_by_id = {loop.id: loop for loop in pending}
        closed = 0
        for item in data.get("closed_loops") or []:
            if not isinstance(item, dict):
                continue
            loop = pending_by_id.get(item.get("id"))
            if loop is None:
                # 模型偶尔会编 id，或把今天新增的当成旧的闭掉。忽略比错标安全。
                logger.warning("回顾给出的闭环 id 不存在: %r", item.get("id"))
                continue
            loop.status = "closed"
            loop.closed_on = day
            loop.closed_note = str(item.get("note") or "").strip() or None
            closed += 1

        opened = 0
        existing = {loop.text.strip() for loop in pending}
        for text in data.get("new_loops") or []:
            text = str(text).strip()
            if not text or text in existing:
                continue
            existing.add(text)
            self.session.add(
                OpenLoop(text=text, opened_on=day, status="open", actor="consolidation")
            )
            opened += 1

        await self.session.commit()
        result.headline = headline
        result.new_loops = opened
        result.closed_loops = closed
        logger.info(
            "回顾完成 %s：%s · %d 条收获 · 新增 %d 挂起 · 闭环 %d",
            day.isoformat(), headline, len(highlights), opened, closed,
        )

    async def _upsert_digest(
        self, day: dt.date, headline: str, highlights: list[str]
    ) -> None:
        """一天一行，重跑整理是覆盖 —— 这是「这一天是什么」的当前答案，不是流水。"""
        digest = await self.session.scalar(
            select(DailyDigest).where(DailyDigest.day == day)
        )
        model = getattr(self.provider, "model_name", "")
        if digest is None:
            self.session.add(
                DailyDigest(
                    day=day, headline=headline, highlights=highlights, model=model
                )
            )
        else:
            digest.headline = headline
            digest.highlights = highlights
            digest.model = model


def _parse_json_object(text: str) -> dict | None:
    """从模型输出里抠出 JSON 对象。

    提示词里写了「不要用 ``` 包裹」，但模型时不时还是会包，或者在前面加一句
    「好的，这是……」。截取第一个 `{` 到最后一个 `}` 能覆盖这两种情况。
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_summary(raw: str, conversation: Conversation) -> _ConversationTake | None:
    """解析摘要的两份输出，解析不了就降级。

    降级路径很重要：摘要是记忆的唯一入口，格式没对上就丢掉整段内容，代价远大于
    存一份格式不佳的摘要。所以解析失败时把原文当 memory 用（旧行为），只是拿不到
    recap，那天的回顾会薄一点。
    """
    data = _parse_json_object(raw)
    if data is None:
        fallback = raw.strip()
        if not fallback or fallback == "无":
            return None
        logger.warning(
            "摘要输出不是 JSON，降级为纯文本: conversation_id=%s", conversation.id
        )
        return _ConversationTake(
            conversation_id=conversation.id,
            title=conversation.title,
            memory=fallback,
            recap="",
        )

    memory = str(data.get("memory") or "").strip()
    recap = str(data.get("recap") or "").strip()
    # 提示词要的是空字符串，但模型常常按老习惯回「无」。
    memory = "" if memory == "无" else memory
    recap = "" if recap == "无" else recap
    loops = [
        str(item).strip() for item in data.get("open_loops") or [] if str(item).strip()
    ]
    if not memory and not recap and not loops:
        return None
    return _ConversationTake(
        conversation_id=conversation.id,
        title=conversation.title,
        memory=memory,
        recap=recap,
        open_loops=loops,
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
