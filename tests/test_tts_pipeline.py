"""句级流水线：边写边读。

整段写完再合成，用户要等「LLM 全程 + TTS 全程」；按句切之后只等
「首句 LLM + 首句 TTS」。这里锁的是切句规则和「第二句起提前合成」这两件事。
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
from app.tts.segment import next_segment
from app.tts.tickets import TicketStore, tickets


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    tickets.clear()


@pytest.fixture
def audio(monkeypatch) -> dict:
    """顶掉网络层，记下每次合成请求的文本。"""
    seen: dict = {"texts": []}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["texts"].append(httpx.Response(200, content=request.content).json()["input"])
        return httpx.Response(
            200, content=b"ID3fake", headers={"content-type": "audio/mpeg"}
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.tts.client.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    return seen


# ---------- 切句 ----------


def test_waits_until_a_full_sentence_arrives() -> None:
    """半句话不切 —— 宁可晚一点出声，也不能把话读断。"""
    assert next_segment("我正在想这件事", 0).text == ""


def test_first_sentence_may_break_at_a_comma() -> None:
    """第一句越早出声，感知等待越短，断在逗号上值得。"""
    segment = next_segment("这个问题有点复杂，我们分几步看", 0)
    assert segment.text == "这个问题有点复杂，"


def test_later_sentences_wait_for_a_hard_stop() -> None:
    """后面的句子有的是时间（用户还在听前一句），走硬边界更连贯。"""
    text = "第一句已经念掉了。"
    assert next_segment(text + "接下来是一段带逗号的话，还没说完", len(text)).text == ""
    full = text + "接下来这句话说完了。"
    assert next_segment(full, len(text)).text == "接下来这句话说完了。"


def test_cursor_advances_without_overlap() -> None:
    """游标必须严丝合缝，重叠会把同一句念两遍，跳过会漏字。"""
    full = "第一句话说完了。第二句话也说完了。第三句话还在写"
    spoken, cursor = [], 0
    while True:
        segment = next_segment(full, cursor)
        if not segment.text:
            break
        spoken.append(segment.text)
        cursor = segment.cursor
    assert spoken == ["第一句话说完了。", "第二句话也说完了。"]

    tail = next_segment(full, cursor, flush=True)
    assert tail.text == "第三句话还在写"


def test_unclosed_code_fence_is_not_spoken() -> None:
    """流式中途围栏只开了一半，里面的内容既可能是代码也可能没写完。"""
    partial = "看这段代码：\n```python\nprint('很长的一行')"
    assert "print" not in next_segment(partial, 0).text

    # 围栏闭合后，块里的内容被清洗掉，块外的话照常念
    closed = partial + "\n```\n就这样。"
    spoken, cursor = [], 0
    while (segment := next_segment(closed, cursor)).text:
        spoken.append(segment.text)
        cursor = segment.cursor
    spoken.append(next_segment(closed, cursor, flush=True).text)

    assert "print" not in "".join(spoken)
    assert "就这样。" in "".join(spoken)


def test_long_run_without_punctuation_is_cut_anyway() -> None:
    """模型写长列表/英文长句时可能几百字不带标点，不能一直等。"""
    segment = next_segment("啊" * 300, 0)
    assert 0 < len(segment.text) <= 120


def test_stops_at_max_chars() -> None:
    """朗读总长上限照旧生效，到顶之后一句都不再切。"""
    full = "一句话。" * 50
    segment = next_segment(full, 0, max_chars=20)
    assert segment.cursor <= 20
    assert next_segment(full, 20, max_chars=20).text == ""


def test_flush_empties_the_tail() -> None:
    """收尾时剩多短都要念掉，否则最后半句永远出不来。"""
    assert next_segment("好", 0, flush=True).text == "好"


# ---------- 接口 ----------


async def test_next_returns_url_only_when_a_sentence_is_ready(
    client: AsyncClient, session: AsyncSession, audio: dict
) -> None:
    await apply(session, {"tts_mode": "auto"}, Settings(tts_mode="off"))

    pending = await client.post("/api/tts/next", json={"text": "这句话还没写完", "cursor": 0})
    assert pending.status_code == 200
    assert pending.json()["url"] is None  # 不是错误，是让前端等下一批增量

    ready = await client.post("/api/tts/next", json={"text": "这句写完了。", "cursor": 0})
    body = ready.json()
    assert body["url"].startswith("/api/tts/stream/")
    assert body["text"] == "这句写完了。"
    assert body["cursor"] == 6


async def test_later_sentences_are_synthesized_before_playback(
    client: AsyncClient, session: AsyncSession, audio: dict
) -> None:
    """第二句起必须在领令牌时就开始合成 —— 等浏览器来取才做，句间会卡顿。"""
    await apply(session, {"tts_mode": "auto"}, Settings(tts_mode="off"))
    full = "第一句话说完了。第二句话也说完了。"

    first = (await client.post("/api/tts/next", json={"text": full, "cursor": 0})).json()
    assert audio["texts"] == []  # 首句走流式，此刻还没发合成请求

    second = (
        await client.post("/api/tts/next", json={"text": full, "cursor": first["cursor"]})
    ).json()
    played = await client.get(second["url"])
    assert played.status_code == 200
    assert audio["texts"] == ["第二句话也说完了。"]  # 播放前就做好了


async def test_stop_drops_pending_sentences(
    client: AsyncClient, session: AsyncSession, audio: dict
) -> None:
    """用户按停止后，队列里剩下的句子不该继续占着合成锁。"""
    await apply(session, {"tts_mode": "auto"}, Settings(tts_mode="off"))
    full = "第一句话说完了。第二句话也说完了。第三句话说完了。"
    cursor = 0
    for _ in range(3):
        body = (
            await client.post("/api/tts/next", json={"text": full, "cursor": cursor})
        ).json()
        cursor = body["cursor"]
        last_url = body["url"]

    dropped = await client.post("/api/tts/stop")
    assert dropped.json()["dropped"] > 0
    assert (await client.get(last_url)).status_code == 404


async def test_next_refuses_when_speech_is_off(
    client: AsyncClient, session: AsyncSession
) -> None:
    await apply(session, {"tts_mode": "off"}, Settings(tts_mode="auto"))
    assert (
        await client.post("/api/tts/next", json={"text": "这句写完了。", "cursor": 0})
    ).status_code == 409


# ---------- 令牌 ----------


def test_eviction_cancels_the_background_task() -> None:
    """被挤掉的令牌不能把合成任务留在后台跑 —— 没人听，还占着锁。"""
    store = TicketStore()
    ticket = store.issue("一句话。", Settings(tts_mode="auto"))
    store.cancel_all()
    assert len(store) == 0
    assert store.redeem(ticket.token) is None
