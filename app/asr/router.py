"""Authenticated browser recording upload and transcription proxy."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.asr.client import ASRBusy, ASRError, transcribe
from app.db.session import get_session
from app.security import require_api_key
from app.settings_store import resolve_settings
from app.tts.cache import list_cached_asr_models
from app.tts.client import TTSError, list_models

router = APIRouter(
    prefix="/api/asr", tags=["asr"], dependencies=[Depends(require_api_key)]
)

_READ_CHUNK = 1024 * 1024
_AUDIO_TYPES = {
    "audio/webm",
    "audio/mp4",
    "audio/ogg",
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/aac",
    "application/ogg",
    "application/octet-stream",
}


class TranscriptionOut(BaseModel):
    text: str


class CachedModelOut(BaseModel):
    id: str
    size_bytes: int


class StatusOut(BaseModel):
    model: str
    language: str
    max_tokens: int
    reachable: bool
    loaded: bool
    models: list[str]
    cached_models: list[CachedModelOut]
    detail: str


@router.get("/status", response_model=StatusOut)
async def asr_status(
    session: AsyncSession = Depends(get_session),
) -> StatusOut:
    """Return the configured model and local cache/service visibility."""
    settings = await resolve_settings(session)
    cached = list_cached_asr_models(settings)
    try:
        models = await list_models(settings)
        detail = ""
        reachable = True
    except TTSError as exc:
        models = []
        detail = str(exc)
        reachable = False
    return StatusOut(
        model=settings.asr_model,
        language=settings.asr_language,
        max_tokens=settings.asr_max_tokens,
        reachable=reachable,
        loaded=settings.asr_model in models,
        models=models,
        cached_models=cached,
        detail=detail,
    )


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK):
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"录音文件不能超过 {limit // (1024 * 1024)} MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/transcriptions", response_model=TranscriptionOut)
async def create_transcription(
    file: UploadFile = File(...),
    model: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
) -> TranscriptionOut:
    """Proxy a browser recording to mlx-audio's transcription endpoint."""
    settings = await resolve_settings(session)
    try:
        if model is not None and model != settings.asr_model:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"语音输入仅支持模型 {settings.asr_model}",
            )

        raw_content_type = file.content_type or "application/octet-stream"
        content_type = raw_content_type.split(";", 1)[0]
        if content_type not in _AUDIO_TYPES:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "只支持音频录音文件")

        audio = await _read_limited(file, settings.asr_max_bytes)
        if not audio:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "录音内容为空")

        text = await transcribe(
            settings,
            audio,
            filename=file.filename or "recording.webm",
            content_type=content_type,
        )
    except ASRBusy as exc:
        # 409 而不是 502：服务是好的，只是这一刻被朗读占着，重试就能成。
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ASRError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    finally:
        await file.close()
    return TranscriptionOut(text=text)
