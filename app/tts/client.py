"""本地文字转语音服务的客户端。

对接的是宿主机上跑的 mlx-audio（OpenAI 兼容的 ``/v1/audio/speech``）。
后端只做**代理**，不让前端直连 8001：

* 配置（模型、音色、语气、语速）统一在数据库设置里，前端不用重复一份
* 前端只有一个 API 源，不用给 TTS 服务额外配 CORS
* 语音要不要开、朗读多长，是服务端策略，浏览器改不了

**串行化**：MLX 后端一次只加载一份模型权重，并发请求只会互相拖慢并放大显存峰值。
单人使用，用一把进程内的锁排队，比让服务自己抖动可控。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

# TTS 和 ASR 共用同一个 MLX 服务。同一时刻只允许一次模型推理，避免加载两份
# 权重时互相拖慢并放大统一内存峰值。ASR 客户端也会导入这把锁。
audio_lock = asyncio.Lock()

MEDIA_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "pcm": "audio/L16",
}


class TTSError(RuntimeError):
    """合成失败。调用方翻译成 502，消息直接给用户看。"""


# ---------- 文本清洗 ----------

_FENCE = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_QUOTE = re.compile(r"^\s{0,3}>\s?", re.M)
_BULLET = re.compile(r"^\s*([-*+]|\d+[.)])\s+", re.M)
_RULE = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$", re.M)
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3}|~~)(?=\S)(.+?)(?<=\S)\1", re.S)
_BLANK_LINES = re.compile(r"\n{3,}")


def plain_text(markdown: str, limit: int = 0) -> str:
    """把模型回复的 Markdown 压成适合朗读的纯文本。

    代码块整段丢掉 —— 念出来既听不懂又能轻松吃掉整个 ``max_tokens``。
    其余标记（标题井号、列表符号、强调星号）只去掉符号、保留文字。

    ``limit`` > 0 时按字符截断，并尽量断在最近的句末，避免半句话戛然而止。
    """
    text = _FENCE.sub(" ", markdown)
    text = _IMAGE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _RULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _QUOTE.sub("", text)
    text = _BULLET.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _BLANK_LINES.sub("\n\n", text).strip()

    if limit > 0 and len(text) > limit:
        head = text[:limit]
        # 往回找最近的句末，找不到就硬截
        cut = max(head.rfind(c) for c in "。！？!?.\n")
        text = (head[: cut + 1] if cut > limit // 2 else head).rstrip()
    return text


# ---------- 调用 ----------


def _payload(settings: Settings, text: str) -> dict[str, object]:
    body: dict[str, object] = {
        "model": settings.tts_model,
        "input": text,
        "lang_code": settings.tts_lang_code,
        "response_format": settings.tts_format,
        # 服务端接受的是倍率，设置项存的是百分比（整数更好在设置页里渲染和校验）
        "speed": settings.tts_speed_percent / 100,
        # 实测同一段话：首字节 6.97s → 1.12s。边合成边发，不等整段做完。
        "stream": settings.tts_stream,
    }
    if settings.tts_voice:
        body["voice"] = settings.tts_voice
    if settings.tts_instruct:
        body["instruct"] = settings.tts_instruct
    return body


class SpeechStream:
    """一次流式合成。**迭代它才会释放锁和连接**，所以拿到就必须消费。

    生命周期比一次函数调用长（要一直活到 body 发完），所以 client / response /
    锁三样东西都挂在这个对象上，在 ``__aiter__`` 的 finally 里一起收。
    """

    def __init__(
        self, client: httpx.AsyncClient, response: httpx.Response, media_type: str
    ) -> None:
        self.client = client
        self.response = response
        self.media_type = media_type
        self._closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        total = 0
        try:
            async for chunk in self.response.aiter_bytes():
                total += len(chunk)
                yield chunk
        finally:
            # 客户端中途断开会让生成器被取消，也要走到这里，否则锁永远不释放
            await self.aclose()
            logger.info("🔊 合成完成 %.1f KB", total / 1024)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.response.aclose()
        await self.client.aclose()
        audio_lock.release()


async def open_stream(settings: Settings, text: str) -> SpeechStream:
    """发起合成，响应头一到就返回，body 留给调用方边收边转发。

    调用方负责先用 :func:`plain_text` 清洗，这里只管发请求。
    **锁在这里获取，在 SpeechStream 消费完时释放** —— 见模块 docstring 的串行化说明。
    """
    text = text.strip()
    if not text:
        raise TTSError("没有可朗读的内容")

    url = settings.tts_base_url.rstrip("/") + "/v1/audio/speech"
    await audio_lock.acquire()
    logger.info(
        "🔊 合成语音 %d 字 → %s%s",
        len(text),
        settings.tts_model,
        " (流式)" if settings.tts_stream else "",
    )

    client = httpx.AsyncClient(timeout=settings.tts_timeout)
    try:
        request = client.build_request("POST", url, json=_payload(settings, text))
        response = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        audio_lock.release()
        logger.warning("语音服务不可用 %s: %s", url, exc)
        raise TTSError(f"连不上语音服务 {settings.tts_base_url}：{exc}") from exc
    except BaseException:
        await client.aclose()
        audio_lock.release()
        raise

    if response.status_code >= 400:
        detail = (await response.aread()).decode(errors="replace")[:300]
        await response.aclose()
        await client.aclose()
        audio_lock.release()
        logger.warning("语音服务返回 %s: %s", response.status_code, detail)
        raise TTSError(f"语音服务返回 {response.status_code}：{detail}")

    # 服务端偶尔不带 Content-Type，按请求的格式兜底
    media_type = response.headers.get("content-type", "")
    if not media_type.startswith("audio/"):
        media_type = MEDIA_TYPES.get(settings.tts_format, "application/octet-stream")
    return SpeechStream(client, response, media_type)


async def synthesize(settings: Settings, text: str) -> tuple[bytes, str]:
    """一次性拿到完整音频，返回 ``(音频字节, Content-Type)``。

    给「要整个 blob」的场景用（设置页试听、下载）。想让浏览器边下边播，
    用 :func:`open_stream`。
    """
    stream = await open_stream(settings, text)
    audio = b"".join([chunk async for chunk in stream])
    if not audio:
        raise TTSError("语音服务返回了空音频")
    return audio, stream.media_type


async def warmup(settings: Settings) -> float:
    """合成一个字，把模型权重加载进 MLX 后端。返回耗时秒数。

    mlx-audio 是**懒加载**的：``/v1/models`` 只列已经加载的，权重要到第一次合成
    才读进显存。不预热的话这十几秒会算在用户第一次点播放的头上，
    看起来就是「语音功能特别慢」，但那只是第一次。

    失败不抛 —— 预热是尽力而为，服务没起来是常态（本地手动起的），
    真正的报错留给用户主动触发的那次合成。
    """
    started = time.monotonic()
    try:
        await synthesize(settings, "嗯")
    except TTSError as exc:
        logger.info("🔊 预热跳过：%s", exc)
        return 0.0
    elapsed = time.monotonic() - started
    logger.info("🔊 语音模型已预热 %.1fs %s", elapsed, settings.tts_model)
    return elapsed


async def list_models(settings: Settings) -> list[str]:
    """探活 + 拿到服务端实际加载的模型清单。连不上时抛 TTSError。"""
    url = settings.tts_base_url.rstrip("/") + "/v1/models"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TTSError(f"连不上语音服务 {settings.tts_base_url}：{exc}") from exc

    return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]


# CustomVoice speakers are stored in the model itself rather than as files in a
# ``voices/`` directory, so mlx-audio's discovery endpoint returns an empty list
# for these models. Keep this small fallback beside the service discovery code;
# other model families continue to use the server-provided catalog.
QWEN3_CUSTOM_VOICES = (
    "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",
    "Ryan", "Aiden", "Ono_Anna", "Sohee",
)


async def list_voices(settings: Settings, model: str) -> list[str]:
    """Return the voices compatible with ``model``.

    mlx-audio 0.4.4+ exposes ``/v1/audio/voices``. Older installations return
    404, and Qwen3 CustomVoice has embedded speakers that the endpoint cannot
    enumerate, so both cases deliberately fall back without making TTS offline.
    """
    fallback = list(QWEN3_CUSTOM_VOICES) if "qwen3-tts" in model.lower() and "customvoice" in model.lower() else []
    url = settings.tts_base_url.rstrip("/") + "/v1/audio/voices"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url, params={"model": model})
            if resp.status_code == 404:
                return fallback
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return fallback

    voices = [
        str(item.get("id") or item.get("name"))
        for item in data.get("data", [])
        if isinstance(item, dict) and (item.get("id") or item.get("name"))
    ]
    return list(dict.fromkeys(voices)) or fallback
