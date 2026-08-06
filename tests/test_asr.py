"""Speech-to-text proxy tests; the real local mlx-audio service is never called."""

from collections.abc import AsyncIterator
from io import BytesIO

import httpx
import pytest
from fastapi import HTTPException, UploadFile
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.asr.client import ASRBusy, ASRError, transcribe
from app.tts.client import audio_lock
from app.asr.router import _read_limited
from app.config import Settings
from app.db.session import get_session
from app.main import create_app


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


@pytest.fixture
def asr_capture(monkeypatch) -> dict[str, object]:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "  这是转写结果  "})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.asr.client.httpx.AsyncClient",
        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs),
    )
    return seen


async def test_client_forwards_openai_compatible_multipart(asr_capture: dict) -> None:
    text = await transcribe(
        Settings(),
        b"webm-audio",
        filename="recording.webm",
        content_type="audio/webm;codecs=opus",
    )

    assert text == "这是转写结果"
    assert str(asr_capture["url"]).endswith("/v1/audio/transcriptions")
    body = asr_capture["body"]
    assert isinstance(body, bytes)
    assert b'name="model"' in body
    assert b"mlx-community/Qwen3-ASR-1.7B-8bit" in body
    assert b'name="language"' in body
    assert b"Chinese" in body
    assert b'name="max_tokens"' in body
    assert b"512" in body
    assert b'filename="recording.webm"' in body
    assert b"webm-audio" in body


async def test_endpoint_returns_transcription(
    client: AsyncClient, asr_capture: dict
) -> None:
    response = await client.post(
        "/api/asr/transcriptions",
        data={"model": "mlx-community/Qwen3-ASR-1.7B-8bit"},
        files={"file": ("recording.webm", b"audio", "audio/webm;codecs=opus")},
    )

    assert response.status_code == 200
    assert response.json() == {"text": "这是转写结果"}
    assert b"audio" in asr_capture["body"]


async def test_status_reports_configured_asr_model(
    client: AsyncClient, monkeypatch
) -> None:
    async def models(_settings: Settings) -> list[str]:
        return ["mlx-community/Qwen3-ASR-1.7B-8bit"]

    monkeypatch.setattr("app.asr.router.list_models", models)
    monkeypatch.setattr(
        "app.asr.router.list_cached_asr_models",
        lambda _settings: [{"id": "mlx-community/Qwen3-ASR-1.7B-8bit", "size_bytes": 42}],
    )
    response = await client.get("/api/asr/status")

    assert response.status_code == 200
    assert response.json() == {
        "model": "mlx-community/Qwen3-ASR-1.7B-8bit",
        "language": "Chinese",
        "max_tokens": 512,
        "reachable": True,
        "loaded": True,
        "models": ["mlx-community/Qwen3-ASR-1.7B-8bit"],
        "cached_models": [
            {"id": "mlx-community/Qwen3-ASR-1.7B-8bit", "size_bytes": 42}
        ],
        "detail": "",
    }


async def test_endpoint_rejects_unconfigured_model(client: AsyncClient) -> None:
    response = await client.post(
        "/api/asr/transcriptions",
        data={"model": "some/other-model"},
        files={"file": ("recording.webm", b"audio", "audio/webm")},
    )
    assert response.status_code == 400
    assert "Qwen3-ASR" in response.json()["detail"]


async def test_endpoint_rejects_non_audio(client: AsyncClient) -> None:
    response = await client.post(
        "/api/asr/transcriptions",
        files={"file": ("notes.txt", b"not audio", "text/plain")},
    )
    assert response.status_code == 415


async def test_endpoint_rejects_empty_recording(client: AsyncClient) -> None:
    response = await client.post(
        "/api/asr/transcriptions",
        files={"file": ("recording.webm", b"", "audio/webm")},
    )
    assert response.status_code == 400
    assert "为空" in response.json()["detail"]


async def test_upload_limit_is_enforced() -> None:
    upload = UploadFile(filename="large.webm", file=BytesIO(b"1234"))
    with pytest.raises(HTTPException) as raised:
        await _read_limited(upload, 3)
    assert getattr(raised.value, "status_code", None) == 413


async def test_service_failure_becomes_bad_gateway(
    client: AsyncClient, monkeypatch
) -> None:
    async def fail(*args, **kwargs):
        raise ASRError("语音服务返回 500：model failed")

    monkeypatch.setattr("app.asr.router.transcribe", fail)
    response = await client.post(
        "/api/asr/transcriptions",
        files={"file": ("recording.m4a", b"audio", "audio/mp4")},
    )
    assert response.status_code == 502
    assert "model failed" in response.json()["detail"]


async def test_transcribe_gives_up_when_tts_holds_the_shared_lock(
    monkeypatch, asr_capture: dict
) -> None:
    """朗读按住 audio_lock 时，转写必须**放弃并说明原因**，而不是静默排队。

    裸 await 那把锁的话，用户在自动朗读期间点麦克风只会看到按钮转圈几十秒。
    """
    monkeypatch.setattr("app.asr.client.LOCK_TIMEOUT", 0.01)
    await audio_lock.acquire()
    try:
        with pytest.raises(ASRBusy):
            await transcribe(
                Settings(),
                b"webm-audio",
                filename="recording.webm",
                content_type="audio/webm",
            )
    finally:
        audio_lock.release()

    # 没拿到锁就不该发请求出去
    assert "url" not in asr_capture
    # 而且不能顺手把别人的锁放掉
    assert audio_lock.locked() is False


async def test_busy_lock_becomes_conflict_not_bad_gateway(
    client: AsyncClient, monkeypatch
) -> None:
    """409 而不是 502：服务是好的，重试就能成，前端提示词也完全不同。"""

    async def busy(*args, **kwargs):
        raise ASRBusy("语音服务正在朗读，请先停止播放再说话")

    monkeypatch.setattr("app.asr.router.transcribe", busy)
    response = await client.post(
        "/api/asr/transcriptions",
        files={"file": ("recording.webm", b"audio", "audio/webm")},
    )
    assert response.status_code == 409
    assert "先停止播放" in response.json()["detail"]
