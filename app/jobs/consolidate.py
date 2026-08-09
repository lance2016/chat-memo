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

from app.agent import build_agent_context
from app.config import Settings, get_settings
from app.db.models import (
    Conversation,
    ConversationSummary,
    DailyDigest,
    Memory,
    MemoryVersion,
    Message,
    OpenLoop,
    live_message,
)
from app.jobs import backfill
from app.jobs.prompts import (
    CONSOLIDATE_ISSUES_TEMPLATE,
    CONSOLIDATE_PROMPT,
    DIGEST_PROMPT,
    DIGEST_SYSTEM,
    SUMMARY_SYSTEM,
)
from app.llm.events import Error
from app.llm.provider import LLMProvider
from app.memory.audit import IndexAudit, audit_index
from app.obs import bind
from app.timeutils import local_day_bounds

logger = logging.getLogger(__name__)


@dataclass
class _ConversationTake:
    """一次摘要调用的全部产出。"""

    conversation_id: int
    title: str
    memory: str
    recap: str
    # 用户原话。只有这一步看得到原始对话，回顾那步拿到的已经是转述。
    quote: str = ""
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
    title: str = ""
    new_loops: int = 0
    closed_loops: int = 0
    digest_failed: bool = False
    # 整理跑完后的索引一致性自检。issues=0 表示索引和实际记忆文件完全对得上；
    # 非 0 时 report 是给人看的一行说明，具体条目下次整理会带进 prompt 让模型修。
    index_issues: int = 0
    index_report: str = ""


def _run_status(result: ConsolidationResult) -> str:
    """这次算成功、跳过、还是失败。

    ⚠️ **agent loop 出错时不抛异常** —— 它把消息作为 `Error` 事件塞进 `detail`，
    整理函数照常返回。只看异常的话这种情况会被记成 `ok`，于是补跑逻辑再也不碰
    这一天，那天的记忆就此永久缺失，而且完全静默。这正是这张表要防的东西。
    """
    # 顺序要紧：摘要失败要先于 skipped 判断。全部摘要都失败时 `skipped` 也是 True，
    # 但那不是「那天没有值得沉淀的内容」，而是**根本没读到输入** ——
    # 记成 skipped 的话这天就再也不会被补跑了。
    if result.failed_summaries or (result.detail and not result.skipped):
        return "failed"
    if result.skipped:
        # 「那天没有值得沉淀的内容」是正常结果，不是失败
        return "skipped"
    return "ok"


def _run_detail(result: ConsolidationResult) -> str:
    parts = [result.detail] if result.detail else []
    if result.failed_summaries:
        parts.append(f"{result.failed_summaries} 个会话摘要失败，这天的输入不完整")
    if result.digest_failed:
        parts.append("每日回顾生成失败（记忆整理本身不受影响）")
    return "；".join(parts)


class Consolidator:
    def __init__(
        self,
        session: AsyncSession,
        provider: LLMProvider,
        settings: Settings | None = None,
    ) -> None:
        """`settings` 应当传生效配置（`resolve_settings` 的结果）。

        不传会落到 `.env` 启动快照 —— 那样设置页里改的 `owner_name` 和自定义指令
        到不了整理任务的 system prompt，而这个偏差没有任何症状：整理照跑，
        只是模型不知道主人叫什么、也看不到用户手写的规矩。
        """
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()

    async def run(self, day: dt.date | None = None) -> ConsolidationResult:
        """Run the job with all model calls tagged as ``consolidate``."""

        day = day or dt.date.today()
        started = time.monotonic()
        with bind(purpose="consolidate"):
            try:
                result = await self._run(day)
            except Exception as exc:
                # 失败也要留痕。**不记的话补跑逻辑会每十分钟重试同一个坏日子**，
                # 而且失败本身没有任何人看得见 —— 整理是核心循环，
                # 「静默不运转」比「报错」危险得多。
                await backfill.record(
                    self.session,
                    day,
                    status="failed",
                    detail=f"{type(exc).__name__}: {exc}",
                    seconds=time.monotonic() - started,
                )
                await self.session.commit()
                raise
            await self._self_check(result)
            await backfill.record(
                self.session,
                day,
                status=_run_status(result),
                detail=_run_detail(result),
                summarized_conversations=result.summarized_conversations,
                memory_writes=result.memory_writes,
                index_issues=result.index_issues,
                seconds=time.monotonic() - started,
            )
            await self.session.commit()
            return result

    async def _self_check(self, result: ConsolidationResult) -> None:
        """整理完机械校验一遍索引。

        放在 `run` 而不是 `_apply` 里，因为「这天没东西可整理」的路径也要查 ——
        索引坏掉和当天有没有对话无关，而连着几天没内容恰恰是最不会有人去看的时候。
        校验本身不修任何东西，问题留给下一次整理的 prompt（`_index_audit`）。
        """
        audit = await self._index_audit()
        result.index_issues = audit.issue_count
        result.index_report = audit.summary()
        if audit.ok:
            logger.info("%s", audit.summary())
        else:
            # warning 而不是 error：记忆本身没坏，是索引和它对不上，下次整理会修。
            logger.warning("%s（下次整理会带进 prompt）", audit.summary())

    async def _index_audit(self) -> IndexAudit:
        """当前记忆快照的索引校验。纯查询，不写任何东西。"""
        return audit_index(list((await self.session.execute(select(Memory))).scalars()))

    async def _run(self, day: dt.date | None = None) -> ConsolidationResult:
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
        # 回顾要看这一天的全部素材，不能只看这次新生成的那几份。摘要是增量的
        # （水位线），重跑一天时大部分会话没有新消息、不会产出 take —— 只喂 takes
        # 的话，越点「重新整理」digest 的素材越少，结果一次比一次差。
        material = await self._day_material(day, conversations, takes)

        if not summaries and not any(t.recap for t in material):
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
            await self._digest(day, material, result)
        except Exception:
            logger.exception("生成每日回顾失败: day=%s", day.isoformat())
            # 回滚回顾这一步的半成品。记忆整理已经 commit 过了，不受影响。
            await self.session.rollback()
            result.digest_failed = True
        return result

    async def _day_material(
        self,
        day: dt.date,
        conversations: list[Conversation],
        takes: list[_ConversationTake],
    ) -> list[_ConversationTake]:
        """这一天喂给回顾的全部素材：本次新产出的 + 之前存下来的。

        重跑必须拿到和首次一样完整的素材，否则「重新整理这一天」就是在用越来越少
        的输入覆盖一份越来越好的回顾。已有摘要只补 recap/quote —— open_loops 是本轮
        抽取结果，旧的已经落库成 OpenLoop 行了，再喂回去会重复。
        """
        fresh = {t.conversation_id: t for t in takes}
        missing = [c for c in conversations if c.id not in fresh]
        material = list(takes)
        if not missing:
            return material

        rows = (
            await self.session.execute(
                select(ConversationSummary)
                .where(ConversationSummary.conversation_id.in_([c.id for c in missing]))
                .order_by(ConversationSummary.id)
            )
        ).scalars()
        # 同一个会话可能有多份增量摘要，留最后一份（覆盖到最新消息的那份）。
        latest = {row.conversation_id: row for row in rows}
        titles = {c.id: c.title for c in missing}
        for conversation_id, row in latest.items():
            if not (row.recap or row.quote):
                continue
            material.append(
                _ConversationTake(
                    conversation_id=conversation_id,
                    title=titles[conversation_id],
                    memory="",
                    recap=row.recap or "",
                    quote=row.quote or "",
                )
            )
        return material

    async def _conversations_on(self, day: dt.date) -> list[Conversation]:
        start, end = local_day_bounds(day)
        stmt = (
            select(Conversation)
            .join(Message, Message.conversation_id == Conversation.id)
            .where(Message.created_at >= start, Message.created_at < end, live_message())
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

        # 被编辑撤下的不摘要。注意这只挡住「还没被摘要过」的那些 —— 撤下发生在
        # 整理之后的话，那段内容早就进了上一份摘要和 L2，这里无能为力。
        stmt = select(Message).where(
            Message.conversation_id == conversation.id, live_message()
        )
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
                quote=take.quote or None,
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

        # 工具和提示词的装配在 app/agent.py，和聊天共用一份。整理的输入是对话摘要，
        # 用不上知识库和时间线 —— 由 purpose 决定，不再在这里手写布尔参数。
        context = await build_agent_context(
            self.session,
            settings=self.settings,
            provider=self.provider,
            purpose="consolidation",
        )
        executor, system = context.executor, context.system
        # 上次留下的索引问题在这里进 prompt。模型只看得到索引本身，看不到「实际有哪些
        # 记忆文件」—— `missing` 这类问题它凭自己永远发现不了，必须由代码算出来告诉它。
        issues = (await self._index_audit()).as_prompt()
        prompt = CONSOLIDATE_PROMPT.format(
            date=day.isoformat(),
            index=INDEX_PATH,
            issues=CONSOLIDATE_ISSUES_TEMPLATE.format(issues=issues) if issues else "",
            summaries="\n\n".join(summaries),
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
        quotes = [f"- 「{t.quote}」（{t.title}）" for t in takes if t.quote]

        prompt = DIGEST_PROMPT.format(
            date=day.isoformat(),
            recaps="\n\n".join(recaps),
            quotes="\n".join(quotes) or "（无）",
            history=await self._recent_history(day),
            new_loops="\n".join(f"- {c}" for c in candidates) or "（无）",
            open_loops="\n".join(
                f"- id={loop.id}（{loop.opened_on.isoformat()} 起）{loop.text}"
                for loop in pending
            )
            or "（无）",
        )
        # 重试一次：模型偶尔会返回空正文或半个 JSON。整理一天只跑一次，
        # 为一次抽风丢掉整份回顾不值得，而多花一次调用的成本可以忽略。
        data = None
        for attempt in (1, 2):
            raw = await self.provider.complete(
                system=DIGEST_SYSTEM, prompt=prompt, max_tokens=4000
            )
            data = _parse_json_object(raw)
            if data is not None:
                break
            logger.warning(
                "回顾输出不是 JSON（第 %d 次）: %r", attempt, raw[:200]
            )
        if data is None:
            raise ValueError("回顾连续两次都没给出 JSON，详见日志")

        headline = str(data.get("headline") or "").strip()
        highlights = [
            str(item).strip()
            for item in data.get("highlights") or []
            if str(item).strip()
        ][:5]
        if not headline:
            raise ValueError("回顾输出缺少 headline")

        # quote 必须是候选里的原文。模型很容易"顺手润色"一下，那就不再是他说的话了 ——
        # 引语的全部价值就在于没被改写，对不上就宁可不要。
        candidates = [t.quote for t in takes if t.quote]
        quote = str(data.get("quote") or "").strip().strip("「」\"'")
        if quote and quote not in candidates:
            logger.warning("回顾给出的引语不在候选里，已丢弃: %r", quote[:60])
            quote = ""
        if not quote and candidates:
            # 模型在这个字段上极度保守，有候选也常常返回空。候选已经在摘要那步
            # 过了「不是指令、不是提问」的筛选，直接取第一条也好过整栏空着。
            quote = candidates[0]

        await self._upsert_digest(
            day,
            headline=headline,
            highlights=highlights,
            title=str(data.get("title") or "").strip(),
            observation=str(data.get("observation") or "").strip(),
            quote=quote,
            echoes=_clean_echoes(data.get("echoes")),
        )

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
        candidate_sources = {
            candidate.strip(): take.conversation_id
            for take in takes
            for candidate in take.open_loops
            if candidate.strip()
        }
        for text in data.get("new_loops") or []:
            text = str(text).strip()
            if not text or text in existing:
                continue
            existing.add(text)
            self.session.add(
                OpenLoop(
                    text=text,
                    opened_on=day,
                    status="open",
                    actor="consolidation",
                    source_conversation_id=candidate_sources.get(text),
                )
            )
            opened += 1

        await self.session.commit()
        result.headline = headline
        result.title = str(data.get("title") or "").strip()
        result.new_loops = opened
        result.closed_loops = closed
        logger.info(
            "回顾完成 %s：%s · %d 条收获 · 新增 %d 挂起 · 闭环 %d",
            day.isoformat(), headline, len(highlights), opened, closed,
        )

    async def _recent_history(self, day: dt.date, days: int = 14) -> str:
        """喂给 echoes 的历史。只给日期和标题 —— 模型只能引用这里出现过的东西。

        再加上一年前的同一天（如果有），anniversary 那类连线全靠它。
        """
        window = day - dt.timedelta(days=days)
        anniversary = day.replace(year=day.year - 1) if day.month != 2 or day.day != 29 else None
        stmt = (
            select(DailyDigest)
            .where(
                DailyDigest.day < day,
                (DailyDigest.day >= window)
                | (DailyDigest.day == anniversary if anniversary else False),
            )
            .order_by(DailyDigest.day.desc())
        )
        rows = list((await self.session.execute(stmt)).scalars())
        if not rows:
            return "（没有更早的回顾，这次不要写 echoes）"
        return "\n".join(
            f"- {row.day.isoformat()}：{row.headline}"
            + (f"（{'、'.join(row.highlights[:3])}）" if row.highlights else "")
            for row in rows
        )

    async def _upsert_digest(
        self,
        day: dt.date,
        *,
        headline: str,
        highlights: list[str],
        title: str = "",
        observation: str = "",
        quote: str = "",
        echoes: list[dict[str, str]] | None = None,
    ) -> None:
        """一天一行，重跑整理是覆盖 —— 这是「这一天是什么」的当前答案，不是流水。"""
        digest = await self.session.scalar(
            select(DailyDigest).where(DailyDigest.day == day)
        )
        values = {
            "headline": headline,
            "highlights": highlights,
            "title": title,
            "observation": observation,
            "quote": quote,
            "echoes": echoes or [],
            "model": getattr(self.provider, "model_name", ""),
        }
        if digest is None:
            self.session.add(DailyDigest(day=day, **values))
        else:
            for key, value in values.items():
                setattr(digest, key, value)


_ECHO_KINDS = {"recurring", "followup", "anniversary"}


def _clean_echoes(raw: object) -> list[dict[str, str]]:
    """只留形状对的连线，最多 3 条。

    kind 认不出来的一律归 recurring 而不是丢掉 —— 内容才是有价值的那部分，
    kind 只影响前端显示哪个图标。
    """
    if not isinstance(raw, list):
        return []
    echoes: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        kind = str(item.get("kind") or "").strip()
        echoes.append(
            {"kind": kind if kind in _ECHO_KINDS else "recurring", "text": text}
        )
    return echoes[:3]


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

    # 提示词要的是空字符串，但模型常常按老习惯回「无」。
    memory, recap, quote = (
        "" if value == "无" else value
        for value in (
            str(data.get(key) or "").strip() for key in ("memory", "recap", "quote")
        )
    )
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
        quote=quote,
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
