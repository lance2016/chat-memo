"""思考开关：全局默认 + 会话级覆盖。

DeepSeek 用 `thinking: {"type": "disabled"}` 关闭（和 Anthropic 同形状）；
`reasoning_effort` 会被静默忽略，别用。实测关掉后工具调用仍正常。
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Conversation
from app.db.session import get_session
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.deepseek_provider import DeepSeekProvider
from app.main import create_app
from tests.fakes import FakeAnthropic, text_turn


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class RecordingCompletions:
    """记录发出去的请求体，不真的调 API。

    **只接受 OpenAI SDK 真实支持的参数** —— 早期版本用 ``**kwargs`` 全盘接收，
    结果代码把 `thinking` 当顶层参数发出去、单测通过、真实 SDK 却报
    "unexpected keyword argument"。假客户端比真接口宽松，就抓不到这类错。
    """

    ALLOWED = {
        "model", "messages", "max_tokens", "stream", "stream_options",
        "tools", "tool_choice", "temperature", "top_p", "extra_body",
        "reasoning_effort", "extra_headers",
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        unknown = set(kwargs) - self.ALLOWED
        if unknown:
            raise TypeError(
                f"AsyncCompletions.create() got an unexpected keyword argument {unknown}"
            )
        self.calls.append(kwargs)
        raise RuntimeError("stop")  # 记完就中断，provider 会转成 Error 事件


def deepseek_with_recorder() -> tuple[DeepSeekProvider, RecordingCompletions]:
    recorder = RecordingCompletions()
    client = AsyncOpenAI(api_key="test", base_url="http://localhost")
    client.chat.completions = recorder  # type: ignore[assignment]
    provider = DeepSeekProvider(
        settings=Settings(deepseek_api_key="test"), client=client
    )
    return provider, recorder


async def drain(provider: Any, **kwargs: Any) -> None:
    async for _ in provider.run(
        system="sys",
        messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        **kwargs,
    ):
        pass


# ---------- DeepSeek ----------


async def test_thinking_on_sends_no_param() -> None:
    """DeepSeek 默认就思考，不传参数即可 —— 少发一个字段。"""
    provider, recorder = deepseek_with_recorder()
    await drain(provider, thinking=True)
    assert "extra_body" not in recorder.calls[0]


async def test_thinking_off_goes_through_extra_body() -> None:
    """thinking 不是 OpenAI 标准字段，当顶层 kwarg 发会被 SDK 拒绝。"""
    provider, recorder = deepseek_with_recorder()
    await drain(provider, thinking=False)

    assert "thinking" not in recorder.calls[0]  # 不能是顶层参数
    assert recorder.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


async def test_thinking_none_follows_global_default() -> None:
    provider, recorder = deepseek_with_recorder()
    provider.settings = Settings(deepseek_api_key="test", deepseek_thinking=False)
    await drain(provider, thinking=None)
    assert recorder.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


async def test_never_sends_reasoning_effort() -> None:
    """DeepSeek 会静默忽略它，发了只会让人误以为生效。"""
    provider, recorder = deepseek_with_recorder()
    await drain(provider, thinking=False)
    assert "reasoning_effort" not in recorder.calls[0]


# ---------- Anthropic ----------


async def test_anthropic_thinking_on_uses_adaptive() -> None:
    provider = AnthropicProvider(
        settings=Settings(anthropic_api_key="t"), client=FakeAnthropic([text_turn("ok")])
    )
    await drain(provider, thinking=True)
    assert provider.client.messages.calls[0]["thinking"]["type"] == "adaptive"


async def test_anthropic_thinking_off_caps_effort() -> None:
    """Opus 5 上「关闭思考」+ xhigh/max 会 400，必须把 effort 压到 high。"""
    provider = AnthropicProvider(
        settings=Settings(anthropic_api_key="t", effort="xhigh"),
        client=FakeAnthropic([text_turn("ok")]),
    )
    await drain(provider, thinking=False)

    call = provider.client.messages.calls[0]
    assert call["thinking"] == {"type": "disabled"}
    assert call["output_config"]["effort"] == "high"


async def test_anthropic_thinking_on_keeps_configured_effort() -> None:
    provider = AnthropicProvider(
        settings=Settings(anthropic_api_key="t", effort="xhigh"),
        client=FakeAnthropic([text_turn("ok")]),
    )
    await drain(provider, thinking=True)
    assert provider.client.messages.calls[0]["output_config"]["effort"] == "xhigh"


# ---------- 会话级覆盖 ----------


async def test_new_conversation_follows_global(
    client: AsyncClient, session: AsyncSession
) -> None:
    body = (await client.post("/api/conversations")).json()
    assert body["thinking"] is None  # null = 跟随全局


async def test_patch_sets_and_clears_override(
    client: AsyncClient, session: AsyncSession
) -> None:
    conversation = Conversation()
    session.add(conversation)
    await session.commit()

    body = (
        await client.patch(
            f"/api/conversations/{conversation.id}", json={"thinking": False}
        )
    ).json()
    assert body["thinking"] is False

    # 传 null 恢复成跟随全局
    body = (
        await client.patch(
            f"/api/conversations/{conversation.id}", json={"thinking": None}
        )
    ).json()
    assert body["thinking"] is None


async def test_patch_title_does_not_touch_thinking(
    client: AsyncClient, session: AsyncSession
) -> None:
    """只传 title 时不能把 thinking 冲掉 —— exclude_unset 的意义就在这。"""
    conversation = Conversation(thinking=False)
    session.add(conversation)
    await session.commit()

    body = (
        await client.patch(
            f"/api/conversations/{conversation.id}", json={"title": "改个名"}
        )
    ).json()
    assert body["title"] == "改个名"
    assert body["thinking"] is False


async def test_settings_endpoint_reports_current_model(client: AsyncClient) -> None:
    body = (await client.get("/api/settings")).json()
    assert body["provider"] in {"anthropic", "deepseek"}
    assert body["thinking_toggle"] is True
    assert isinstance(body["thinking_default"], bool)


# ---------- 整理任务的模型覆盖 ----------


def test_model_override_does_not_mutate_global_settings() -> None:
    """settings 是 lru_cache 出来的全局单例，覆盖必须复制，不能就地改。"""
    from app.llm.factory import get_provider

    base = Settings(provider="deepseek", deepseek_api_key="t", deepseek_model="flash")
    provider = get_provider(base, model_override="pro")

    assert provider.settings.deepseek_model == "pro"
    assert base.deepseek_model == "flash"  # 原对象没被污染


def test_empty_override_keeps_configured_model() -> None:
    from app.llm.factory import get_provider

    base = Settings(provider="deepseek", deepseek_api_key="t", deepseek_model="flash")
    assert get_provider(base, model_override="").settings.deepseek_model == "flash"
