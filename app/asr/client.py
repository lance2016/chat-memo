"""Client for mlx-audio's OpenAI-compatible transcription endpoint."""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import Settings
from app.tts.client import audio_lock

logger = logging.getLogger(__name__)

# 抢 audio_lock 最多等这么久。这把锁是和 TTS 共用的，而朗读会**按住整段合成**
# （auto 模式下可长达几十秒）。裸 await 的话，用户在朗读时点麦克风只会看到
# 按钮一直转圈、没有任何解释 —— 转写是同步交互，宁可立刻说清楚也不要静默排队。
LOCK_TIMEOUT = 2.0


class ASRError(RuntimeError):
    """The local transcription service could not produce usable text."""


class ASRBusy(ASRError):
    """语音服务正被朗读占用。调用方翻译成 409，让前端提示「先停止播放」。"""


async def transcribe(
    settings: Settings,
    audio: bytes,
    *,
    filename: str,
    content_type: str,
) -> str:
    """Upload one browser recording and return its transcription text."""
    if not audio:
        raise ASRError("录音内容为空")

    url = settings.tts_base_url.rstrip("/") + "/v1/audio/transcriptions"
    # 超时不能进 try/finally —— 没拿到锁就去 release 会把别人的锁放掉。
    try:
        await asyncio.wait_for(audio_lock.acquire(), LOCK_TIMEOUT)
    except TimeoutError as exc:
        logger.info("🎙️ 语音服务被占用，放弃转写（等待 %.1fs）", LOCK_TIMEOUT)
        raise ASRBusy("语音服务正在朗读，请先停止播放再说话") from exc
    logger.info(
        "🎙️ 转写录音 %.1f KB → %s",
        len(audio) / 1024,
        settings.asr_model,
    )
    try:
        form = {
            "model": settings.asr_model,
            "max_tokens": str(settings.asr_max_tokens),
        }
        if settings.asr_language and settings.asr_language != "Auto":
            form["language"] = settings.asr_language
        async with httpx.AsyncClient(timeout=settings.asr_timeout) as client:
            response = await client.post(
                url,
                data=form,
                files={"file": (filename, audio, content_type)},
            )
    except httpx.HTTPError as exc:
        logger.warning("语音转写服务不可用 %s: %s", url, exc)
        raise ASRError(f"连不上语音服务 {settings.tts_base_url}：{exc}") from exc
    finally:
        audio_lock.release()

    if response.status_code >= 400:
        detail = response.text[:300]
        logger.warning("语音转写服务返回 %s: %s", response.status_code, detail)
        raise ASRError(f"语音服务返回 {response.status_code}：{detail}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ASRError("语音服务返回了无法解析的转写结果") from exc

    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ASRError("没有识别到可用文字")
    result = text.strip()
    logger.info("🎙️ 转写完成 %d 字", len(result))
    return result
