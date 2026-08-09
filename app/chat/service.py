from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import debug
from app.attachments import store
from app.attachments.hydrate import (
    AttachmentHydrator,
    has_refs,
    placeholder_hydrate,
    ref_block,
)
from app.config import Settings, get_settings
from app.db.models import Conversation, Message, live_message
from app.db.session import get_sessionmaker
from app.llm.events import (
    AgentEvent,
    AssistantTurn,
    Done,
    Error,
    TextDelta,
    ToolResult,
    ToolResultTurn,
    ToolUse,
)
from app.llm.provider import LLMProvider, ToolExecutor
from app.llm.target import ModelTarget
from app.llm.title import TitleClient
from app.logging_setup import dim, ok_mark
from app.obs import bind

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "新对话"

# 正文结束时仍未完成的标题会转到后台；后台最多再等这么久，不阻塞回答流。
TITLE_TIMEOUT = 5.0
_background_title_tasks: set[asyncio.Task[None]] = set()

# 标题只有十几个字，但不要卡得太死：万一模型忽略了「关闭思考」，
# 预算太紧会只剩思考、正文被截断，结果是标题静默变空。
TITLE_MAX_TOKENS = 500

INTERRUPTED_RESULT = "（上一轮被中断，该工具未执行完成。如仍需要，请重新调用。）"


def extract_text(blocks: list[dict[str, Any]]) -> str:
    """抽出 content 里的纯文本，供搜索用。

    只取 text 块 —— thinking 和 tool_use 参数进了搜索索引全是噪音，
    搜某个词不该命中模型的内部推理。
    """
    return "\n".join(
        b.get("text", "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text"
    ).strip()


def _attachment_seed(blocks: list[dict[str, Any]]) -> str:
    names = [
        b.get("filename", "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "attachment_ref"
    ]
    names = [name for name in names if name]
    return f"（用户发来图片：{'、'.join(names)}）" if names else ""


def _preview(text: str, limit: int = 60) -> str:
    """日志里只放一行预览 —— 完整内容在数据库里，不该刷屏。"""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _summarize_turn(done: Done | None, tools: int, seconds: float) -> str:
    parts = [f"{seconds:.1f}s"]
    if tools:
        parts.append(f"{tools} 工具")
    usage = done.usage if done else {}
    total = usage.get("total_tokens") or (
        usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    )
    if total:
        parts.append(f"{total} tok")
    cached = usage.get("prompt_cache_hit_tokens") or usage.get(
        "cache_read_input_tokens", 0
    )
    if cached:
        parts.append(f"缓存 {cached}")
    return " · ".join(parts)


def _message_chars(message: dict[str, Any]) -> int:
    """一条消息发给模型时的字符体量。

    直接量 JSON 序列化后的长度：block 的结构五花八门（text / thinking / tool_use 的
    入参 / tool_result 的正文），逐类去数迟早漏掉一种，而漏掉的那种恰恰是最容易膨胀的
    工具结果。序列化多算的那点结构开销，方向是偏保守的，正合适。
    """
    return len(json.dumps(message, ensure_ascii=False))


def approximate_tokens(value: Any) -> int:
    """Estimate token size without pretending to be a provider tokenizer.

    The application supports several providers and most compatible endpoints do
    not expose their tokenizer. Chinese/non-ASCII text is therefore counted
    closer to one token per character, while ASCII runs use a conservative
    four-characters-per-token approximation. The result is deliberately marked
    as estimated by the API.
    """
    if value is None:
        return 0
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    other_chars = len(text) - ascii_chars
    return max(1, int((ascii_chars + 3) // 4 + other_chars))


def context_breakdown(
    system: str,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return an explainable, provider-neutral context cost breakdown.

    Provider usage gives us the exact aggregate input count only. This view is
    an estimate of the components that made up that input, useful for finding
    an oversized prompt, tool result, or attachment without leaking content.
    """
    conversation_tokens = 0
    tool_message_tokens = 0
    image_tokens = 0
    image_count = 0

    for message in messages:
        content = message.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        for block in blocks:
            if not isinstance(block, dict):
                conversation_tokens += approximate_tokens(block)
                continue
            kind = block.get("type")
            if kind in {"image", "image_url", "input_image", "attachment_ref"}:
                image_count += 1
                # The media tokenization is model-specific. Include the small
                # JSON envelope and a visible placeholder estimate, rather
                # than showing zero and hiding image cost altogether.
                image_tokens += approximate_tokens(block) + 512
            elif kind in {"tool_use", "tool_result", "tool_call"}:
                tool_message_tokens += approximate_tokens(block)
            else:
                conversation_tokens += approximate_tokens(block)

        for call in message.get("tool_calls", []) or []:
            tool_message_tokens += approximate_tokens(call)

    return [
        {"key": "system", "label": "系统提示词", "tokens": approximate_tokens(system), "estimated": True},
        {"key": "tools", "label": "工具与智能体", "tokens": approximate_tokens(tools) + tool_message_tokens, "estimated": True},
        {"key": "conversation", "label": "对话消息", "tokens": conversation_tokens, "estimated": True},
        {"key": "images", "label": "图片与附件", "tokens": image_tokens, "items": image_count, "estimated": True},
        {"key": "connectors", "label": "连接器 / MCP", "tokens": 0, "items": 0, "estimated": True},
    ]


def _is_orphan_result(message: dict[str, Any]) -> bool:
    """这条 user 消息是不是「只有工具结果」—— 它的 tool_use 在裁剪中被丢掉了。"""
    content = message.get("content")
    if not isinstance(content, list) or not content:
        return False
    return all(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def trim_history(
    messages: list[dict[str, Any]], max_chars: int
) -> list[dict[str, Any]]:
    """把历史压进字符预算，从**最老的一端**整条整条地丢。

    不做就会出事：历史每轮全量发给模型，长会话线性膨胀，最终撞上下文窗口，
    而那之后该会话每条消息都 400 —— 永久损坏。

    从旧端丢有两个必须守住的边界，破了任何一条都是 400：

    1. 保留窗口的**第一条必须是 user**。Anthropic 明确要求 messages 以 user 开头，
       而按体量截断很容易正好切在 assistant 上。
    2. 第一条不能是「只有 tool_result」的 user 消息 —— 配对的 tool_use 已经被丢了，
       孤立的结果块两家 API 都不收。

    两条合起来就是：定好切点后一路向后走，直到第一条是「真正的用户发言」。
    最后一条无论如何都保留（哪怕它自己就超预算），否则这轮没有输入可发。
    """
    if max_chars <= 0 or not messages:
        return messages

    total = 0
    start = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        total += _message_chars(messages[index])
        if total > max_chars and index < len(messages) - 1:
            break
        start = index

    while start < len(messages) - 1 and (
        messages[start]["role"] != "user" or _is_orphan_result(messages[start])
    ):
        start += 1

    if start == 0:
        return messages

    logger.info("✂ 历史超出 %d 字预算，丢弃最老的 %d 条消息", max_chars, start)
    return messages[start:]


def history_window_stats(
    messages: list[dict[str, Any]], max_chars: int
) -> dict[str, int]:
    """Return the effective history window using the exact production trim rules."""
    retained = sanitize_history(trim_history(messages, max_chars))
    real_user_messages = sum(
        1
        for message in retained
        if message.get("role") == "user" and not _is_orphan_result(message)
    )
    return {
        "history_chars": sum(_message_chars(message) for message in retained),
        "history_budget_chars": max_chars,
        "retained_messages": len(retained),
        "retained_turns": real_user_messages,
        "trimmed_messages": max(0, len(messages) - len(retained)),
    }


def sanitize_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """补齐没有配对结果的 tool_use。

    对话中途被打断（点停止、关标签页、断网、开发时热重载）会留下「模型发起了工具调用
    但结果没落库」的历史。两家 API 都要求每个 tool_use 必须有对应的 tool_result，
    否则整个会话之后每条消息都 400 —— 会话等于永久损坏。

    这里给缺失的调用补一条 is_error 结果，让模型知道那次调用没成功，可以自己重试。
    """
    out: list[dict[str, Any]] = []
    skip_next = False

    for i, message in enumerate(messages):
        if skip_next:
            skip_next = False
            continue
        out.append(message)

        if message["role"] != "assistant":
            continue
        pending = [
            block["id"]
            for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        if not pending:
            continue

        following = messages[i + 1] if i + 1 < len(messages) else None
        existing: list[dict[str, Any]] = []
        answered: set[str] = set()
        if following is not None and following["role"] == "user":
            existing = [
                block
                for block in following["content"]
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ]
            if existing and len(existing) == len(following["content"]):
                # 下一条纯粹是工具结果，合并进去而不是再插一条消息
                answered = {block.get("tool_use_id") for block in existing}
                skip_next = True

        missing = [tid for tid in pending if tid not in answered]
        if not missing:
            if skip_next:
                out.append(following)
            continue

        repaired = [
            *existing,
            *(
                {
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "content": INTERRUPTED_RESULT,
                    "is_error": True,
                }
                for tid in missing
            ),
        ]
        out.append({"role": "user", "content": repaired})

    return out


def build_runtime_context(
    settings: Settings, now: dt.datetime | None = None, model: str = ""
) -> str:
    """注入到本轮 user 消息前的运行时信息。

    **不能放 system prompt** —— 那是 prompt cache 的稳定前缀，插入时间戳会让整段缓存失效。
    放在 user 侧还有个好处：只有当前这轮带时间，历史里不会留下一堆过期时间戳。

    没有这段的话，模型不知道今天几号（写不了 timeline、说不出「上周」），
    也不知道自己实际跑在哪个模型上（会瞎猜成 Claude）。
    """
    now = now or dt.datetime.now()
    weekday = "一二三四五六日"[now.weekday()]
    # 模型名由调用方从 provider 拿（`provider.model_name`），这里不再按厂商字段猜 ——
    # 猜的那份在换 provider 或用模型目录选模型之后就会和实际调用的对不上。
    model = model or ModelTarget.from_settings(settings).model_id
    return (
        "<runtime_context>\n"
        f"当前时间：{now:%Y-%m-%d} 星期{weekday} {now:%H:%M}\n"
        f"当前时区：{os.environ.get('TZ', 'Asia/Shanghai')}\n"
        f"你实际运行在：{settings.provider} / {model}\n"
        "以上是系统注入的运行时信息，不是用户的话。除非用户问起，不要主动提及。\n"
        "</runtime_context>"
    )

_XML_TAG = re.compile(r"<[^>]{1,40}>")
_QUOTES = "“”‘’\"'《》「」 　"


def _clean_title(raw: str) -> str:
    """把模型输出收拾成一行可用的标题。

    模型偶尔会带上引号、把 <thinking> 之类的标签漏进正文，或者多写几行说明。
    收拾不出内容就返回空串，让调用方保留默认标题。
    """
    if not raw:
        return ""
    # 标签成对出现时连内容一起去掉，否则只去标签本身。
    text = re.sub(r"<thinking>.*?</thinking>", " ", raw, flags=re.S | re.I)
    text = _XML_TAG.sub(" ", text)
    for line in text.splitlines():
        candidate = line.strip().strip(_QUOTES).strip()
        if candidate:
            return candidate[:40]
    return ""

TITLE_SYSTEM = (
    "你为对话生成标题。只输出标题本身，不要引号、不要标点结尾、不要任何解释。"
    "标题用中文，不超过 16 个字，概括用户想解决的事。"
)


class ChatService:
    def __init__(
        self,
        session: AsyncSession,
        provider: LLMProvider,
        executor: ToolExecutor | None = None,
        settings: Settings | None = None,
        title_client: TitleClient | None = None,
        model_profile_id: int | None = None,
        hydrator: AttachmentHydrator | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.executor = executor
        self.settings = settings or get_settings()
        # 由 router 装配（解析视觉档案要查数据库）。None = 这条链路上没有附件，
        # 历史里万一有引用块会退化成占位文本，不会把原始 ref 发给模型。
        self.hydrator = hydrator
        # 由调用方（router）决定标题走不走智谱。这里**不**自己去问全局配置：
        # 那样任何只注入了假 provider 的测试都会跟着开发机的 .env 走真实网络请求。
        # None = 用聊天 provider 兜底（同样关思考）。
        self.title_client = title_client
        self.model_profile_id = model_profile_id

    async def load_history(self, conversation_id: int) -> list[dict[str, Any]]:
        """按原样取回历史。

        content 存的就是 Anthropic content block 数组，thinking 签名和 tool_use/tool_result
        的配对关系都在里面，直接回传即可。

        先按预算裁剪、再补配对：裁剪只动旧的一端（可能留下孤立的 tool_result，
        ``trim_history`` 自己处理），``sanitize_history`` 管的是新的一端
        （中断留下的、没有结果的 tool_use）。两者互不干扰。

        被编辑撤下的消息不参与 —— 这是最要紧的一个 ``live_message()`` 调用点，
        漏了它编辑就等于没编辑。
        """
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id, live_message())
            .order_by(Message.id)
        )
        rows = (await self.session.execute(stmt)).scalars()
        history = [{"role": row.role, "content": row.content} for row in rows]
        return sanitize_history(trim_history(history, self.settings.history_max_chars))

    async def _hydrate(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """引用块换成图片或描述。**发给模型之前的最后一步。**

        没有 hydrator 时也不能把 ref 原样发出去 —— 那对模型是一段没有意义的
        JSON，它会当成用户说的话。退化成占位文本至少是句实话。
        """
        if not has_refs(messages):
            return messages
        if self.hydrator is None:
            return placeholder_hydrate(messages)
        return await self.hydrator.hydrate(messages)

    async def _persist(
        self,
        conversation_id: int,
        role: str,
        content: list[dict[str, Any]],
        usage: dict[str, Any] | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            usage=usage,
            model_profile_id=self.model_profile_id,
            search_text=extract_text(content),
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def _touch(self, conversation: Conversation) -> None:
        """新消息不会自动更新会话的 updated_at —— onupdate 只在该行本身被改动时触发。

        侧边栏按 updated_at 倒序排，不显式 touch 的话聊过的会话永远浮不上来。
        """
        conversation.updated_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    def _continue_title_in_background(
        self, conversation_id: int, task: asyncio.Task[str], route: str
    ) -> None:
        async def finish() -> None:
            try:
                title = await asyncio.wait_for(task, TITLE_TIMEOUT)
                if not title:
                    return
                async with get_sessionmaker()() as session:
                    conversation = await session.get(Conversation, conversation_id)
                    if conversation is not None and conversation.title == DEFAULT_TITLE:
                        conversation.title = title
                        await session.commit()
                logger.info("🏷 %s [%s · 后台]", title, route)
            except TimeoutError:
                logger.info("conv#%s 后台标题生成超过 %ss", conversation_id, TITLE_TIMEOUT)
            except Exception:
                logger.exception("后台生成标题失败 [%s]", route)

        background = asyncio.create_task(finish())
        _background_title_tasks.add(background)
        background.add_done_callback(_background_title_tasks.discard)

    async def stream_reply(
        self,
        *,
        conversation: Conversation,
        system: str,
        user_text: str,
        attachment_ids: list[int] | None = None,
    ) -> AsyncIterator[AgentEvent | tuple[str, dict[str, Any]]]:
        """Run a chat turn under one Phoenix session and chat purpose."""

        with bind(session_id=conversation.id, purpose="chat"):
            inner = self._stream_reply(
                conversation=conversation,
                system=system,
                user_text=user_text,
                attachment_ids=attachment_ids or [],
            )
            try:
                async for event in inner:
                    yield event
            finally:
                # Closing the public generator must also close the inner one;
                # otherwise its DB cleanup runs later during fixture teardown.
                await inner.aclose()

    async def _stream_reply(
        self,
        *,
        conversation: Conversation,
        system: str,
        user_text: str,
        attachment_ids: list[int] | None = None,
    ) -> AsyncIterator[AgentEvent | tuple[str, dict[str, Any]]]:
        """跑一轮对话，边流式产出事件边落库。

        额外会产出 ``("title", {...})`` 这种元组，用于把生成的标题推给前端。
        """
        # provider 不知道自己在为哪个会话干活，又不该为了调试改它的签名
        debug.current_conversation.set(conversation.id)
        history = await self.load_history(conversation.id)

        attachment_ids = attachment_ids or []
        attachments = await store.load_many(self.session, attachment_ids)
        # 引用块排在文本前面：先看到图再看到问题，和人贴图的顺序一致。
        # 落库的是引用；发给模型的那一份既带运行时上下文、又把引用换成真内容 ——
        # 两处不一致都是有意的，见 attachments/hydrate.py。
        user_content: list[dict[str, Any]] = [
            ref_block(attachments[aid]) for aid in attachment_ids if aid in attachments
        ]
        if user_text:
            user_content.append({"type": "text", "text": user_text})

        user_message = await self._persist(conversation.id, "user", user_content)
        await store.attach_to_message(
            self.session,
            attachment_ids,
            message_id=user_message.id,
            conversation_id=conversation.id,
        )
        await self.session.commit()

        outgoing = [
            {
                "type": "text",
                "text": build_runtime_context(
                    self.settings, model=self.provider.model_name
                ),
            },
            *user_content,
        ]
        messages = await self._hydrate(
            [*history, {"role": "user", "content": outgoing}]
        )

        last_message_id = user_message.id
        failed = False
        completed = False
        done: Done | None = None
        started = time.monotonic()
        tool_count = 0
        # 用户已经看到的正文。中断时要落库，否则刷新页面内容就没了。
        streamed: list[str] = []
        # 标题和正文互不依赖，就让它俩并行 —— 串在正文之后会让用户在
        # 「话已经说完」和 done 之间多等一次模型调用，白白转圈。
        # 只并行模型调用，写库留到最后：session 不能并发使用。
        # 纯图片消息没有正文可概括，退而用文件名 —— 标题模型收到空字符串
        # 只会编一个和这次对话无关的标题。
        title_seed = user_text or _attachment_seed(user_content)
        title_task = (
            asyncio.create_task(self._complete_title(title_seed))
            if conversation.title == DEFAULT_TITLE and title_seed
            else None
        )
        title_route = (
            self.title_client.route
            if self.title_client is not None
            else f"{self.settings.provider} 不思考"
        )
        logger.info(
            "→ conv#%s %s %s",
            conversation.id,
            dim(f"[{self.settings.provider}{'' if conversation.thinking is not False else ' 不思考'}]"),
            _preview(user_text),
        )

        try:
            async for event in self._drive(conversation, system, messages, streamed):
                if isinstance(event, ToolUse):
                    tool_count += 1

                if isinstance(event, AssistantTurn):
                    row = await self._persist(
                        conversation.id, "assistant", event.content, event.usage
                    )
                    last_message_id = row.id
                    # 这一轮已完整落库，之前累积的流式文本就不用再兜底了
                    streamed.clear()
                    await self._touch(conversation)
                    continue  # 内部事件，不推给前端

                if isinstance(event, ToolResultTurn):
                    row = await self._persist(conversation.id, "user", event.content)
                    last_message_id = row.id
                    await self._touch(conversation)
                    continue

                if isinstance(event, Error):
                    failed = True
                    logger.error("✗ conv#%s %s", conversation.id, event.message)

                if isinstance(event, Done):
                    # 先压住：若标题已经完成，它的事件必须赶在 done 前面发出去。
                    # 尚未完成的标题不会在这里等待，而是在正文结束后转到后台。
                    done = event
                    continue

                yield event
            completed = True
        finally:
            if not completed:
                # 客户端断开 / 点了停止 / 热重载。用户已经看到的内容必须留下，
                # 否则刷新页面就凭空消失了。
                await self._save_interrupted(conversation, streamed)
                if title_task is not None:
                    title_task.cancel()

        if not failed:
            logger.info(
                "← conv#%s %s",
                conversation.id,
                dim(_summarize_turn(done, tool_count, time.monotonic() - started)),
            )

        if title_task is not None:
            if failed:
                title_task.cancel()
            elif not title_task.done():
                self._continue_title_in_background(conversation.id, title_task, title_route)
            else:
                try:
                    title = title_task.result()
                except Exception:
                    logger.exception("生成标题失败 [%s]", title_route)
                    title = ""
                if title:
                    conversation.title = title
                    await self.session.commit()
                    yield ("title", {"title": title})

        if done is not None:
            yield done
            yield ("message_id", {"message_id": last_message_id})

    async def _drive(
        self,
        conversation: Conversation,
        system: str,
        messages: list[dict[str, Any]],
        streamed: list[str],
    ) -> AsyncIterator[AgentEvent]:
        """跑 provider，顺带记日志、累积已流出的正文。事件原样透传。"""
        async for event in self.provider.run(
            system=system,
            messages=messages,
            executor=self.executor,
            thinking=conversation.thinking,
        ):
            if isinstance(event, TextDelta):
                streamed.append(event.text)
            elif isinstance(event, ToolUse):
                logger.info(
                    "  ⚙ %s %s",
                    event.input.get("command", event.name),
                    dim(str(event.input.get("path", event.input.get("old_path", "")))),
                )
            elif isinstance(event, ToolResult) and not event.ok:
                logger.warning("  %s %s", ok_mark(False), event.summary)
            yield event

    async def _save_interrupted(
        self, conversation: Conversation, streamed: list[str]
    ) -> None:
        """把中断时已经流给用户的正文落库。

        只存 text，**不存 thinking** —— Anthropic 的 thinking 块必须带签名才能回传，
        半截的思考没有签名，下一轮会 400。思考内容本来也是临时的。
        """
        text = "".join(streamed).strip()
        if not text:
            return
        await self._persist(
            conversation.id,
            "assistant",
            [{"type": "text", "text": text}],
            {"interrupted": True},
        )
        await self._touch(conversation)
        logger.warning("⚠ conv#%s 被中断，已保留 %d 字", conversation.id, len(text))

    async def _complete_title(self, first_text: str) -> str:
        """只管问模型要标题，不碰 session —— 它和正文并行跑。

        两条路都**关掉思考**：标题是一句话概括，推理只会浪费 token、降低标题
        赶在正文前完成的概率，并让转入后台的任务占用资源更久。
        配了硅基流动或智谱标题 key 就走专用小模型，否则退回聊天 provider。
        """
        with bind(purpose="title"):
            prompt = f"用户的第一条消息：\n\n{first_text}"
            client = self.title_client
            # 走哪条路、花了多久，都要能从日志里直接看出来 —— 否则「标题到底有没有
            # 用那个小模型」只能靠猜，配错了 key 也只表现为「又变慢了」。
            route = client.route if client is not None else f"{self.settings.provider} 不思考"
            started = time.monotonic()
            try:
                if client is not None:
                    raw = await client.complete(
                        system=TITLE_SYSTEM, prompt=prompt, max_tokens=TITLE_MAX_TOKENS
                    )
                else:
                    raw = await self.provider.complete(
                        system=TITLE_SYSTEM,
                        prompt=prompt,
                        max_tokens=TITLE_MAX_TOKENS,
                        thinking=False,
                    )
            except Exception:
                # 标题只是锦上添花，失败不该影响这次对话。
                logger.exception("生成标题失败 [%s]", route)
                return ""

            title = _clean_title(raw)
            logger.info(
                "  🏷 %s %s",
                title or "（空，保留默认标题）",
                dim(f"[{route} · {time.monotonic() - started:.1f}s]"),
            )
            return title
