"""附件上传与取回。

正文走 `Response` 直接返回，**不挂 `StaticFiles`** —— 整个应用没有任何静态目录
挂载，开这个口子等于让一个可写挂载目录变成公开可读的。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.attachments import store
from app.attachments.errors import (
    AttachmentNotFound,
    InvalidAttachment,
    InvalidAttachmentPath,
)
from app.attachments.paths import content_disposition
from app.db.session import get_session
from app.security import require_api_key
from app.settings_store import resolve_settings

router = APIRouter(
    prefix="/api/attachments",
    tags=["attachments"],
    dependencies=[Depends(require_api_key)],
)

_READ_CHUNK = 1024 * 1024


class AttachmentOut(BaseModel):
    id: int
    # image | file。前端靠它决定渲染缩略图还是文件卡片，别让它去猜 mime。
    kind: str
    filename: str
    mime: str
    bytes: int
    width: int
    height: int


@contextmanager
def _as_http_error() -> Iterator[None]:
    """附件层的可预期失败翻成 4xx。抄 skills/router.py 的做法，别在每个接口里重复。"""
    try:
        yield
    except AttachmentNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (InvalidAttachment, InvalidAttachmentPath) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK):
        total += len(chunk)
        # 边读边数，不信 Content-Length —— 那是客户端说了算的
        if total > limit:
            # 这是外层硬闸，图片和文本共用同一个上限（取两者中大的那个）。
            # 分类型的上限在 `store.save_upload` 里，那里才知道这是什么。
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"文件不能超过 {limit // (1024 * 1024)} MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=AttachmentOut)
async def upload_attachment(
    file: UploadFile = File(...),
    conversation_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> AttachmentOut:
    """上传一个附件（图片，或 txt / md 文本）。

    此刻还不知道它会挂到哪条消息上，发送时才回填。是图还是文本由
    `store.save_upload` 判定并写进 `kind`，前端照它渲染。
    """
    settings = await resolve_settings(session)
    try:
        data = await _read_limited(file, settings.attachment_max_bytes)
        with _as_http_error():
            row = await store.save_upload(
                session,
                settings,
                data,
                filename=file.filename or "",
                conversation_id=conversation_id,
            )
        await session.commit()
    finally:
        await file.close()

    return AttachmentOut(
        id=row.id,
        kind=row.kind,
        filename=row.filename,
        mime=row.mime,
        bytes=row.bytes,
        width=row.width,
        height=row.height,
    )


@router.get("/{attachment_id}")
async def download_attachment(
    attachment_id: int, session: AsyncSession = Depends(get_session)
) -> Response:
    """返回正文。前端取到之后转成 blob URL 喂给 `<img>`。

    ⚠️ `<img src>` 带不了 `X-API-Key`，所以前端**不能**直接把这个地址写进 src ——
    要 authed fetch 一次再 createObjectURL。这里保持和其余接口一致的鉴权，
    不为了省那一步开一条免鉴权路径。
    """
    settings = await resolve_settings(session)
    with _as_http_error():
        row = await store.get_row(session, attachment_id)
        data = store.read_blob(settings, row.sha256)

    return Response(
        content=data,
        media_type=row.mime or "application/octet-stream",
        headers={
            # 内容寻址，同一个 id 的正文永远不会变 —— 可以放心让浏览器长期缓存。
            "Cache-Control": "private, max-age=31536000, immutable",
            "Content-Disposition": content_disposition(row.filename),
        },
    )
