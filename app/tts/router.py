"""语音接口：合成 + 探活。

音频不落库也不落盘 —— 内容本来就在 ``messages`` 里，重放一次的成本远低于
管理一堆音频文件的生命周期。

**两条路，按前端要干什么选**：

* ``POST /speech`` → 整段音频。要 blob（缓存、下载、试听）时用
* ``POST /prepare`` → 一个播放 URL，``GET`` 它边下边播。要立刻听到声音时用

第二条存在的唯一理由是浏览器：只有 `<audio src>` 这条路径能渐进播放，
而它发的是不带请求头的 GET。实测同一段话首字节 6.97s → 1.12s。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.session import get_session
from app.security import require_api_key
from app.settings_store import resolve_settings
from app.tts.client import (
    TTSError,
    list_models,
    open_stream,
    plain_text,
    synthesize,
    warmup,
)
from app.tts.segment import next_segment
from app.tts.tickets import TTL_SECONDS, tickets

router = APIRouter(
    prefix="/api/tts", tags=["tts"], dependencies=[Depends(require_api_key)]
)

# `<audio src>` 发的 GET 带不了 X-API-Key，所以流式播放单独挂一个不校验的 router，
# 凭证换成 URL 里那个一次性令牌。理由见 tickets.py。
public = APIRouter(prefix="/api/tts", tags=["tts"])


class SpeechIn(BaseModel):
    text: str = Field(min_length=1)
    # 设置页试听用：临时覆盖当前配置，不写库
    voice: str | None = None
    instruct: str | None = None
    # 关掉截断，用于「朗读全文」按钮
    truncate: bool = True


class PrepareOut(BaseModel):
    url: str
    expires_in: int


class NextIn(BaseModel):
    """句级流水线的入参。见 ``/next`` 的说明。"""

    # 到目前为止**累计的全文**（原始 Markdown），不是增量
    text: str = ""
    cursor: int = 0
    # 模型说完了：把剩下的尾巴全部切出来，不管够不够长
    flush: bool = False


class NextOut(BaseModel):
    # 切不出完整句子时为 None —— 不是出错，是让调用方等下一批增量
    url: str | None = None
    text: str = ""
    cursor: int = 0
    expires_in: int = TTL_SECONDS


class StatusOut(BaseModel):
    mode: str
    stream: bool
    enabled: bool
    base_url: str
    model: str
    voice: str
    format: str
    max_chars: int
    reachable: bool
    models: list[str]
    detail: str


async def _prepare(
    payload: SpeechIn, session: AsyncSession
) -> tuple[Settings, str]:
    """两个入口共用的前置：解析配置、套用试听覆盖、清洗文本。"""
    settings = await resolve_settings(session)
    if settings.tts_mode == "off":
        raise HTTPException(status.HTTP_409_CONFLICT, "语音播放已关闭")

    if payload.voice is not None or payload.instruct is not None:
        settings = settings.model_copy(
            update={
                k: v
                for k, v in (
                    ("tts_voice", payload.voice),
                    ("tts_instruct", payload.instruct),
                )
                if v is not None
            }
        )

    text = plain_text(payload.text, settings.tts_max_chars if payload.truncate else 0)
    return settings, text


@router.post("/speech")
async def speech(
    payload: SpeechIn, session: AsyncSession = Depends(get_session)
) -> Response:
    """把一段文字合成完整音频返回。

    入参是**原始 Markdown**，清洗在服务端做：前端渲染用的和朗读用的是两套文本，
    让前端各自 strip 一遍迟早会不一致。

    要「立刻听到声音」用 ``/prepare``；这个接口会等整段合成完才返回。
    """
    settings, text = await _prepare(payload, session)
    try:
        audio, media_type = await synthesize(settings, text)
    except TTSError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return Response(
        content=audio,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/prepare", response_model=PrepareOut)
async def prepare(
    payload: SpeechIn, session: AsyncSession = Depends(get_session)
) -> PrepareOut:
    """换一个播放 URL 回去，前端把它塞给 `<audio src>` 就能边下边播。

    这一步**不合成**，只登记文本，所以是即时返回的。真正的合成发生在
    浏览器 GET 那个 URL 的时候。
    """
    settings, text = await _prepare(payload, session)
    if not text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有可朗读的内容")

    ticket = tickets.issue(text, settings)
    suffix = settings.tts_format
    return PrepareOut(
        url=f"/api/tts/stream/{ticket.token}.{suffix}", expires_in=TTL_SECONDS
    )


@router.post("/next", response_model=NextOut)
async def next_(
    payload: NextIn, session: AsyncSession = Depends(get_session)
) -> NextOut:
    """句级流水线：从累计全文里切出下一句，换一个播放 URL 回去。

    **这是「边写边读」的入口**。整段回答写完再合成，用户要等
    ``LLM 全程 + TTS 全程``；按句流水线之后只等``首句 LLM + 首句 TTS``，
    后面的句子在听前一句时就做好了。

    用法（每收到一批流式增量调一次，切不出来就返回 ``url: null``）::

        cursor = 0
        cursor, url = next(text=累计全文, cursor=cursor)   # 有 url 就入队
        ...
        cursor, url = next(text=全文, cursor=cursor, flush=True)  # 收尾

    切句和清洗都在服务端，前端只负责把 ``cursor`` 原样传回来。
    第一句走流式（首字节最快），后面的句子领令牌时就在后台合成好了 —— 见 tickets.py。
    """
    settings = await resolve_settings(session)
    if settings.tts_mode == "off":
        raise HTTPException(status.HTTP_409_CONFLICT, "语音播放已关闭")

    segment = next_segment(
        payload.text,
        payload.cursor,
        flush=payload.flush,
        max_chars=settings.tts_max_chars,
    )
    if not segment.text:
        return NextOut(cursor=segment.cursor)

    # 第一句要的是尽快出声，走流式；后面的句子要的是接得上，提前做好。
    ticket = tickets.issue(segment.text, settings, prefetch=payload.cursor > 0)
    return NextOut(
        url=f"/api/tts/stream/{ticket.token}.{settings.tts_format}",
        text=segment.text,
        cursor=segment.cursor,
    )


@router.post("/stop")
async def stop() -> dict[str, int]:
    """丢弃所有还没播的句子。用户按停止时调。

    不调也不会出错（令牌会自己过期），但队列里剩下的句子会继续占着合成锁，
    拖慢下一次朗读。
    """
    return {"dropped": tickets.cancel_all()}


@router.post("/warmup")
async def warmup_(session: AsyncSession = Depends(get_session)) -> dict[str, float]:
    """把模型权重加载进后端。见 client.warmup —— 失败也返回 200。"""
    settings = await resolve_settings(session)
    return {"seconds": await warmup(settings)}


@public.get("/stream/{token}")
async def stream_speech(token: str) -> StreamingResponse:
    """边合成边发。**不校验 X-API-Key** —— 令牌本身就是凭证，见 tickets.py。

    令牌用一次即失效，所以这个 URL 不能重播；要重播就重新 ``/prepare``。
    """
    # URL 上的扩展名只是给浏览器看的（有些实现会据此猜解码器），这里剥掉
    ticket = tickets.redeem(token.rsplit(".", 1)[0])
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "播放链接已失效，请重新获取")

    if ticket.task is not None:
        # 领令牌时就开始做了，多半已经做完 —— 直接给完整音频，中间不留空隙。
        try:
            audio, media_type = await ticket.task
        except TTSError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        return Response(
            content=audio, media_type=media_type, headers={"Cache-Control": "no-store"}
        )

    try:
        stream = await open_stream(ticket.settings, ticket.text)
    except TTSError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return StreamingResponse(
        stream,
        media_type=stream.media_type,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/status", response_model=StatusOut)
async def status_(session: AsyncSession = Depends(get_session)) -> StatusOut:
    """设置页用：当前语音配置 + 服务在不在。

    探活是实时打的（5 秒超时），因为本地 TTS 服务是手动起的，
    「配置对但没开进程」是最常见的失败方式，得让界面能直接看出来。
    """
    settings = await resolve_settings(session)
    models: list[str] = []
    detail = ""
    reachable = True
    try:
        models = await list_models(settings)
    except TTSError as exc:
        reachable, detail = False, str(exc)

    # 空列表不是错：/v1/models 只列**当前已加载**的模型，首次合成时才懒加载。
    # 只有明确列出了别的模型、唯独没有配置里这个，才值得提醒。
    if reachable and models and settings.tts_model not in models:
        detail = f"服务端未加载 {settings.tts_model}"

    return StatusOut(
        mode=settings.tts_mode,
        stream=settings.tts_stream,
        enabled=settings.tts_mode != "off",
        base_url=settings.tts_base_url,
        model=settings.tts_model,
        voice=settings.tts_voice,
        format=settings.tts_format,
        max_chars=settings.tts_max_chars,
        reachable=reachable,
        models=models,
        detail=detail,
    )
