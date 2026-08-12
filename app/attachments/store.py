"""附件的落盘与读取。

**行和文件是多对一**：磁盘按 sha256 内容寻址，同一张图反复贴只写一次；
数据库每次上传各有一行（各自的文件名、各自挂在哪条消息上）。
这个不对称是有意的 —— 去重要按内容，而「这条消息里的那张图叫什么」要按次。

⚠️ 删除附件行**不能**顺手删磁盘文件：别的行可能指向同一个摘要。
第一版根本不提供删除（见 docs/internals.md 的孤儿附件那条）。
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attachments import text as text_kind
from app.attachments.errors import AttachmentNotFound, InvalidAttachment
from app.attachments.image import sniff
from app.attachments.paths import SHA256_PATTERN, blob_path, safe_filename
from app.config import Settings
from app.db.models import Attachment

logger = logging.getLogger(__name__)


def root_dir(settings: Settings) -> Path:
    return Path(settings.attachments_path)


def clear_blobs(settings: Settings) -> int:
    """删除附件目录中所有内容寻址的正文，保留未知文件以避免误删挂载内容。"""
    root = root_dir(settings)
    if not root.is_dir():
        return 0

    deleted = 0
    for shard in root.iterdir():
        if not shard.is_dir() or not re.fullmatch(r"[0-9a-f]{2}", shard.name):
            continue
        for path in shard.iterdir():
            if not path.is_file():
                continue
            digest = path.name.removesuffix(".partial")
            if not SHA256_PATTERN.fullmatch(digest) or not digest.startswith(shard.name):
                continue
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                logger.warning("清理附件正文失败：%s (%s)", path, exc)
        try:
            shard.rmdir()
        except OSError:
            # 目录里有未知文件时保留整个目录，避免破坏用户挂载内容。
            pass
    return deleted


def _check_size(size: int, limit: int, label: str) -> None:
    if size <= limit:
        return
    # 上限不到 1MB 时按 KB 说，否则「不能超过 0 MB」这种话会出现在界面上
    readable = (
        f"{limit // (1024 * 1024)} MB" if limit >= 1024 * 1024 else f"{limit // 1024} KB"
    )
    raise InvalidAttachment(f"{label}不能超过 {readable}")


async def save_upload(
    session: AsyncSession,
    settings: Settings,
    data: bytes,
    *,
    filename: str,
    conversation_id: int | None = None,
) -> Attachment:
    """校验、落盘、插行。返回的行还没 commit，交给调用方决定事务边界。"""
    if not data:
        raise InvalidAttachment("文件内容为空")

    # 先按扩展名分流，再各自校验：两条路的体积上限和错误消息都不一样，
    # 混在一起的症状是传一个 .md 收到「只支持 PNG / JPEG / GIF / WebP 图片」。
    # ⚠️ 扩展名只用来选路，**不构成准入** —— 两条路各自都要自己确认内容。
    if text_kind.looks_like_text(filename):
        kind = "file"
        _check_size(len(data), settings.attachment_text_max_bytes, "文本文件")
        mime, _ = text_kind.decode(data, filename=filename)
        width = height = 0
    else:
        kind = "image"
        _check_size(len(data), settings.attachment_max_bytes, "图片")
        # 类型以文件头为准，不信浏览器报的 content_type
        try:
            mime, width, height = sniff(data)
        except InvalidAttachment as exc:
            # 一次传好几个文件时，光说「只支持 PNG / JPEG / GIF / WebP」定位不到是哪个。
            # 头部的十六进制是给日志看的：HEIC / AVIF / SVG 都会走到这里，
            # 而它们的 content_type 都是 image/*，光看前端根本分不出来。
            logger.warning(
                "附件被拒：%s（%d 字节，头 %s）—— %s",
                filename or "(无名)",
                len(data),
                data[:12].hex(" "),
                exc,
            )
            raise InvalidAttachment(
                f"{safe_filename(filename, fallback='这个文件')}：{exc}"
            ) from exc

    digest = hashlib.sha256(data).hexdigest()

    target = blob_path(root_dir(settings), digest)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再改名：写一半被打断时，留下的是个临时文件而不是一个
        # 内容残缺却顶着正确摘要名的文件 —— 后者之后永远不会被重写（已存在就跳过）。
        staging = target.with_name(f"{digest}.partial")
        staging.write_bytes(data)
        staging.replace(target)

    attachment = Attachment(
        conversation_id=conversation_id,
        kind=kind,
        filename=safe_filename(filename, fallback=kind),
        mime=mime,
        bytes=len(data),
        sha256=digest,
        width=width,
        height=height,
    )
    session.add(attachment)
    await session.flush()
    return attachment


async def get_row(session: AsyncSession, attachment_id: int) -> Attachment:
    row = await session.get(Attachment, attachment_id)
    if row is None:
        raise AttachmentNotFound(f"附件 #{attachment_id} 不存在")
    return row


def read_blob(settings: Settings, digest: str) -> bytes:
    """读正文。文件丢了要明确报错 —— 静默返回空会变成「模型收到一张空图」。"""
    path = blob_path(root_dir(settings), digest)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise AttachmentNotFound(
            f"附件正文 {digest[:12]} 在磁盘上找不到：{exc}"
        ) from exc


def read_text(settings: Settings, row: Attachment) -> str:
    """读一个 `kind="file"` 的正文并解码。

    每次现读现解，不缓存也不加列：文本本来就在磁盘上，而解码是纯 CPU 的几毫秒。
    图片那边要 `vision_description` 列是因为那一步要花模型的钱，这里不是。
    """
    return text_kind.decode(read_blob(settings, row.sha256), filename=row.filename)[1]


async def has_images(session: AsyncSession, attachment_ids: list[int]) -> bool:
    """这批附件里有没有真正需要看图能力的。

    存在的理由是聊天入口那道「当前模型看不了图」的拦截：它不能对着
    `attachment_ids` 非空就拦，否则给纯文本模型传一个 .md 会被判成传图。
    """
    if not attachment_ids:
        return False
    found = await session.scalar(
        select(Attachment.id)
        .where(Attachment.id.in_(set(attachment_ids)), Attachment.kind == "image")
        .limit(1)
    )
    return found is not None


async def attach_to_message(
    session: AsyncSession,
    attachment_ids: list[int],
    *,
    message_id: int,
    conversation_id: int,
) -> None:
    """把上传时还悬空的附件行挂到刚落库的消息上。"""
    if not attachment_ids:
        return
    rows = (
        await session.execute(
            select(Attachment).where(Attachment.id.in_(attachment_ids))
        )
    ).scalars()
    for row in rows:
        row.message_id = message_id
        row.conversation_id = conversation_id


async def load_many(
    session: AsyncSession, attachment_ids: list[int]
) -> dict[int, Attachment]:
    """按 id 批量取，返回 id → 行。缺的 id 不会出现在结果里。"""
    if not attachment_ids:
        return {}
    rows = (
        await session.execute(
            select(Attachment).where(Attachment.id.in_(set(attachment_ids)))
        )
    ).scalars()
    return {row.id: row for row in rows}


async def cached_description(
    session: AsyncSession, digest: str
) -> tuple[str, str] | None:
    """同一张图（同摘要）别的行算过描述没有。返回 (描述, 模型名)。

    描述按内容复用而不是按行：同一张截图贴在两个会话里，没有理由算两次。
    """
    row = await session.scalar(
        select(Attachment)
        .where(Attachment.sha256 == digest, Attachment.vision_description != "")
        .order_by(Attachment.id)
        .limit(1)
    )
    if row is None:
        return None
    return row.vision_description, row.vision_model
