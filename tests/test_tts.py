"""文字转语音：文本清洗、请求组装、失败翻译。

不打真实的 8001，用 httpx 的 MockTransport 顶掉网络层。
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.settings_store import apply
from app.tts.client import TTSError, plain_text, synthesize


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def tts_settings(**kw) -> Settings:
    return Settings(tts_mode="manual", **kw)


@pytest.fixture
def capture(monkeypatch) -> dict:
    """拦下发往 TTS 服务的请求，回一段假音频。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = httpx.Response(200, content=request.content).json()
        return httpx.Response(200, content=b"ID3fake", headers={"content-type": "audio/mpeg"})

    real = httpx.AsyncClient

    def fake_client(**kwargs):
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.tts.client.httpx.AsyncClient", fake_client)
    return seen


# ---------- 文本清洗 ----------


def test_code_fences_dropped() -> None:
    """代码块念出来听不懂，还能轻松吃掉整个 max_tokens。"""
    out = plain_text("先看这段：\n```python\nprint('x')\n```\n就这样")
    assert "print" not in out
    assert "先看这段" in out and "就这样" in out


def test_markup_stripped_but_text_kept() -> None:
    out = plain_text("## 标题\n- **重点**是 `code`\n[链接](http://x.com)")
    assert "#" not in out and "*" not in out and "`" not in out
    assert "标题" in out and "重点" in out and "code" in out
    assert "链接" in out and "http" not in out


def test_truncation_breaks_at_sentence_end() -> None:
    text = "啊" * 30 + "。" + "还有很多没念完的内容" * 10
    out = plain_text(text, limit=40)
    assert out.endswith("。")
    assert len(out) == 31


def test_truncation_hard_cuts_when_break_is_too_early() -> None:
    """断点在前半段时宁可硬截 —— 否则一句「好的。」会把整段回复砍没。"""
    out = plain_text("好的。" + "啊" * 100, limit=30)
    assert len(out) == 30


def test_no_limit_keeps_everything() -> None:
    assert len(plain_text("啊" * 100, limit=0)) == 100


# ---------- 请求组装 ----------


async def test_payload_maps_settings(capture: dict) -> None:
    settings = tts_settings(
        tts_model="m", tts_voice="Vivian", tts_lang_code="Chinese",
        tts_instruct="温柔一点", tts_speed_percent=90, tts_format="mp3",
    )
    audio, media_type = await synthesize(settings, "你好")

    assert audio == b"ID3fake"
    assert media_type == "audio/mpeg"
    assert capture["url"].endswith("/v1/audio/speech")
    assert capture["json"] == {
        "model": "m", "input": "你好", "lang_code": "Chinese",
        "response_format": "mp3", "speed": 0.9, "stream": True,
        "voice": "Vivian", "instruct": "温柔一点",
    }


async def test_empty_voice_and_instruct_omitted(capture: dict) -> None:
    """空字符串不能当成「音色叫空字符串」发出去。"""
    await synthesize(tts_settings(tts_voice="", tts_instruct=""), "你好")
    assert "voice" not in capture["json"] and "instruct" not in capture["json"]


async def test_blank_text_rejected() -> None:
    with pytest.raises(TTSError):
        await synthesize(tts_settings(), "   ")


async def test_service_error_becomes_tts_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="model not loaded")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.tts.client.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    with pytest.raises(TTSError, match="500"):
        await synthesize(tts_settings(), "你好")


async def test_connection_error_names_the_url(monkeypatch) -> None:
    """连不上是最常见的失败（服务没起），错误信息要带地址。"""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.tts.client.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    with pytest.raises(TTSError, match="127.0.0.1:8001"):
        await synthesize(tts_settings(), "你好")


# ---------- 接口 ----------


async def test_speech_returns_audio(
    client: AsyncClient, session: AsyncSession, capture: dict
) -> None:
    await apply(session, {"tts_mode": "manual"}, Settings())
    await session.commit()

    resp = await client.post("/api/tts/speech", json={"text": "## 你好\n世界"})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.content == b"ID3fake"
    # 清洗在服务端做，Markdown 标记不该漏进去
    assert capture["json"]["input"] == "你好\n世界"


async def test_speech_rejected_when_mode_off(client: AsyncClient) -> None:
    """默认 off。关着还能合成的话，「只出文字」这个设置就没意义了。"""
    resp = await client.post("/api/tts/speech", json={"text": "你好"})
    assert resp.status_code == 409


async def test_preview_overrides_do_not_persist(
    client: AsyncClient, session: AsyncSession, capture: dict
) -> None:
    await apply(session, {"tts_mode": "manual", "tts_voice": "Vivian"}, Settings())
    await session.commit()

    await client.post(
        "/api/tts/speech", json={"text": "你好", "voice": "Ethan"}
    )
    assert capture["json"]["voice"] == "Ethan"

    await client.post("/api/tts/speech", json={"text": "你好"})
    assert capture["json"]["voice"] == "Vivian"


async def test_status_reports_unreachable(client: AsyncClient, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.tts.client.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    body = (await client.get("/api/tts/status")).json()
    assert body["reachable"] is False and body["detail"]
    assert body["mode"] == "off" and body["enabled"] is False


async def test_status_flags_model_not_loaded(client: AsyncClient, monkeypatch) -> None:
    """服务在线但没加载配置里那个模型 —— 界面要能区分这两种失败。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "other-model"}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.tts.client.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    body = (await client.get("/api/tts/status")).json()
    assert body["reachable"] is True
    assert "未加载" in body["detail"]
    assert body["models"] == ["other-model"]


async def test_status_empty_model_list_is_not_an_error(
    client: AsyncClient, monkeypatch
) -> None:
    """/v1/models 只列当前已加载的模型，服务刚起来时是空的（首次合成才懒加载）。

    把空列表当成「模型没装」会让状态灯长期误报红。
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "list", "data": []})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.tts.client.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    body = (await client.get("/api/tts/status")).json()
    assert body["reachable"] is True and body["detail"] == ""
