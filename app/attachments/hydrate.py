"""把消息里的 `attachment_ref` 换成模型真正能吃的内容。

**为什么消息里存的是引用而不是图片本身**，两个理由，任意一个都够：

1. `trim_history` 的预算按 `len(json.dumps(message))` 算字符。一张 base64 图
   动辄几十万字符，能一口吃掉整个 12 万的历史预算 —— 存 ref 才让那个预算是诚实的。
2. `messages.content` 会进 `pg_dump`，而备份每天一次、保留十几份。

所以「存的」和「发出去的」在这里第二次故意不一致（第一次是 runtime context）。
唯一的规矩是：**发出去之前必须 hydrate，且历史和当前轮都要**。漏掉历史那一处的症状
很隐蔽 —— 第一轮好好的，追问时模型突然不知道你在说哪张图。

图片 hydrate 出什么，只看 `target.supports_vision` 一个判据：

- 能看图 → 真正的 image block
- 看不了 → 视觉模型写的那段描述文本（懒生成，按 sha256 复用）

文本附件（`kind="file"`，目前只有 txt / md）不参与上面那个分支：任何模型都读得了
文本，所以它无条件展开成正文。**唯一的约束是体量** —— 见 `TEXT_INLINE_CHARS`。
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.attachments import store, vision
from app.attachments.errors import AttachmentError
from app.config import Settings
from app.db.models import Attachment
from app.llm.factory import get_provider
from app.llm.target import ModelTarget

logger = logging.getLogger(__name__)

REF_TYPE = "attachment_ref"

# 描述里带上一点对话上下文，视觉模型才知道该重点抄哪部分。
# 不能多：这段会跟着每张图各发一次，而且它对描述质量的边际收益下降很快。
CONTEXT_CHARS = 600

# 一个文本附件最多往上下文里放多少字符。
#
# 这个数必须存在：`trim_history` 的预算是 12 万字符，而上传上限是 512KB ——
# 一个文件就能把整段历史挤掉，且症状是「模型忘了前面聊过什么」，很难联想到附件。
# 超出的部分**不是静默丢掉**，末尾会留一句明说截断了，模型据此知道自己看的是片段。
# 完整读取要等 roadmap 第 10 条的 `doc_read`，那才是长文档的正确形态。
TEXT_INLINE_CHARS = 30000


def ref_block(attachment: Attachment) -> dict[str, Any]:
    """落库用的引用块。

    冗余存 filename 是有意的：渲染气泡、以及附件行万一被清理掉时，
    至少还知道「这里原来有张叫什么的图」。
    """
    return {
        "type": REF_TYPE,
        "id": attachment.id,
        "kind": attachment.kind,
        "filename": attachment.filename,
        # These small facts let clients describe the attachment without
        # decoding it. The binary itself remains on disk and is only read by
        # the provider hydrate step.
        "mime": attachment.mime,
        "bytes": attachment.bytes,
        "width": attachment.width,
        "height": attachment.height,
    }


def collect_ref_ids(messages: list[dict[str, Any]]) -> list[int]:
    """扫出所有引用到的附件 id，按出现顺序去重。"""
    seen: dict[int, None] = {}
    for message in messages:
        blocks = message.get("content")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == REF_TYPE:
                value = block.get("id")
                if isinstance(value, int):
                    seen.setdefault(value, None)
    return list(seen)


def has_refs(messages: list[dict[str, Any]]) -> bool:
    return bool(collect_ref_ids(messages))


def _placeholder_block(block: dict[str, Any]) -> dict[str, Any]:
    label = "文件" if block.get("kind") == "file" else "图片"
    name = block.get("filename") or ""
    return {"type": "text", "text": f"[{label} {name}]".replace(" ]", "]")}


def placeholder_hydrate(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """不查库、不读盘的退化版：引用块换成一句「这里有张图」。

    给没有装配 hydrator 的链路兜底。**原始 ref 块绝不能发给模型** ——
    那是一段内部 JSON，模型只会把它当成用户说的话。
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        blocks = message.get("content")
        if not isinstance(blocks, list):
            out.append(message)
            continue
        replaced = [
            _placeholder_block(block)
            if isinstance(block, dict) and block.get("type") == REF_TYPE
            else block
            for block in blocks
        ]
        out.append({**message, "content": replaced})
    return out


class AttachmentHydrator:
    """一次对话轮里所有 hydrate 的入口。

    ⚠️ 每轮新建一个：`target` 是这一轮解析出来的，缓存的描述也只在这一轮内复用。
    跨轮的复用靠数据库（`vision_description` 列），不靠这个对象。
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        target: ModelTarget,
        vision_target: ModelTarget | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.target = target
        self.vision_target = vision_target

    async def hydrate(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """返回一份可以直接发给模型的消息列表。没有引用时原样返回。"""
        ids = collect_ref_ids(messages)
        if not ids:
            return messages

        rows = await store.load_many(self.session, ids)
        if not self.target.supports_vision:
            images = [row for row in rows.values() if row.kind == "image"]
            await self._ensure_descriptions(images, messages)

        return [self._hydrate_message(message, rows) for message in messages]

    def _hydrate_message(
        self, message: dict[str, Any], rows: dict[int, Attachment]
    ) -> dict[str, Any]:
        blocks = message.get("content")
        if not isinstance(blocks, list):
            return message
        if not any(
            isinstance(b, dict) and b.get("type") == REF_TYPE for b in blocks
        ):
            return message

        out: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != REF_TYPE:
                out.append(block)
                continue
            out.extend(self._expand(block, rows.get(block.get("id", -1))))
        return {**message, "content": out}

    def _expand(
        self, block: dict[str, Any], row: Attachment | None
    ) -> list[dict[str, Any]]:
        label = "文件" if block.get("kind") == "file" else "图片"
        name = block.get("filename") or label
        if row is None:
            # 行没了但引用还在。给一句实话而不是静默删掉 —— 模型至少知道
            # 这里曾经有张图，用户问起时能说清楚，而不是装作无事发生。
            return [{"type": "text", "text": f"[{label} {name}：已不可用]"}]

        if row.kind == "file":
            return [self._expand_text(row, name)]

        if self.target.supports_vision:
            try:
                data = store.read_blob(self.settings, row.sha256)
            except AttachmentError as exc:
                logger.warning("附件 #%s 正文读取失败：%s", row.id, exc)
                return [{"type": "text", "text": f"[图片 {name}：正文丢失]"}]
            return [vision.image_block(row.mime, data)]

        # ⚠️ 编号必须出现在文本里：`image_ask` 要靠它指认是哪张图，
        # 而模型能看到的只有这段文字。去掉它，那个工具就永远调不动。
        if row.vision_description:
            return [
                {
                    "type": "text",
                    "text": (
                        f"[图片 #{row.id} {name}]\n{row.vision_description}\n"
                        f"（以上是另一个模型看图后写的描述。需要描述里没写的细节时，"
                        f"用 image_ask 带着具体问题重看 #{row.id}。）"
                    ),
                }
            ]
        return [
            {
                "type": "text",
                "text": (
                    f"[图片 #{row.id} {name}：无法识别。"
                    "当前聊天模型看不了图，也没有配置可用的视觉模型档案]"
                ),
            }
        ]

    def _expand_text(self, row: Attachment, name: str) -> dict[str, Any]:
        """文本附件 → 一个带围栏的 text 块。

        围栏不是为了好看，是为了给模型划出「这是用户上传的资料，不是他说的话」
        的边界 —— 上传的文件是第四个内容来源，里面写的任何指令都不是用户的指令。
        """
        try:
            body = store.read_text(self.settings, row)
        except AttachmentError as exc:
            logger.warning("附件 #%s 正文读取失败：%s", row.id, exc)
            return {"type": "text", "text": f"[文件 {name}：正文丢失]"}

        note = ""
        if len(body) > TEXT_INLINE_CHARS:
            body = body[:TEXT_INLINE_CHARS]
            note = (
                f"\n（以上只是这个文件的前 {TEXT_INLINE_CHARS} 个字符，"
                f"全文共 {row.bytes} 字节，后面的部分没有放进来。"
                "回答时要说清楚你看到的是片段。）"
            )

        fence = _fence_for(body)
        return {
            "type": "text",
            "text": (
                f"[文件 #{row.id} {name}]\n"
                f"{fence}\n{body}\n{fence}{note}"
            ),
        }

    async def _ensure_descriptions(
        self, rows: list[Attachment] | Any, messages: list[dict[str, Any]]
    ) -> None:
        """给还没有描述的附件补上。已经有的一律不动。"""
        pending = [row for row in rows if not row.vision_description]
        if not pending:
            return
        if self.vision_target is None:
            # 不抛异常：入口处（chat/router）已经拦过「带新图但没配视觉模型」，
            # 能走到这里的是历史消息里的老图，为它整轮失败不合理。
            logger.info("未配置视觉模型档案，%d 张图只能以占位文本进入上下文", len(pending))
            return

        context = _recent_text(messages)
        provider = get_provider(self.settings, target=self.vision_target)
        changed = False
        for row in pending:
            # 同一张图别的行算过就直接抄，不重复花钱
            cached = await store.cached_description(self.session, row.sha256)
            if cached is not None:
                row.vision_description, row.vision_model = cached
                row.vision_at = dt.datetime.now(dt.UTC)
                changed = True
                continue
            try:
                data = store.read_blob(self.settings, row.sha256)
                text = await vision.describe(
                    provider, mime=row.mime, data=data, context=context
                )
            except AttachmentError as exc:
                logger.warning("附件 #%s 正文读取失败：%s", row.id, exc)
                continue
            except Exception:
                # 看图失败不该让整轮对话失败 —— 退化成占位文本，用户还能继续聊。
                logger.exception("附件 #%s 的视觉描述生成失败", row.id)
                continue
            if not text:
                logger.warning("附件 #%s 的视觉描述为空", row.id)
                continue
            row.vision_description = text
            row.vision_model = self.vision_target.model_id
            row.vision_at = dt.datetime.now(dt.UTC)
            changed = True
            logger.info(
                "👁 %s → %d 字描述 [%s]",
                row.filename or f"#{row.id}",
                len(text),
                self.vision_target.model_id,
            )
        if changed:
            await self.session.commit()


def _fence_for(body: str) -> str:
    """给正文选一段够长的反引号围栏。

    固定用三个的话，一个自己就带代码块的 Markdown 文件会把围栏提前闭合，
    后半段正文就跑到围栏外面去了 —— 那正是「用户上传的资料」和「用户的话」
    混在一起的情形。规则和 CommonMark 一致：比正文里最长的一串再多一个。
    """
    longest = 0
    run = 0
    for char in body:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _recent_text(messages: list[dict[str, Any]], limit: int = CONTEXT_CHARS) -> str:
    """取最近几条消息的纯文本，给视觉模型当上下文。从最新的一端往回取。"""
    parts: list[str] = []
    total = 0
    for message in reversed(messages):
        blocks = message.get("content")
        if not isinstance(blocks, list):
            continue
        text = "\n".join(
            b.get("text", "")
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
        if not text:
            continue
        parts.append(f"{message.get('role', 'user')}: {text}")
        total += len(text)
        if total >= limit:
            break
    return "\n".join(reversed(parts))[-limit:]
