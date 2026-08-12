"""思考开关：全局默认 + 会话级覆盖。

DeepSeek 的开关通过 ``extra_body.thinking`` 传入；开启时同时发送官方支持的
``reasoning_effort=low/high/max``，关闭时不能残留 effort。
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
        settings=Settings(provider="deepseek", deepseek_api_key="test"), client=client
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


async def test_thinking_on_explicitly_enables_builtin_deepseek() -> None:
    """The composer switch must not depend on an upstream model's default."""
    provider, recorder = deepseek_with_recorder()
    await drain(provider, thinking=True)
    assert recorder.calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert recorder.calls[0]["reasoning_effort"] == "high"


async def test_thinking_off_goes_through_extra_body() -> None:
    """thinking 不是 OpenAI 标准字段，当顶层 kwarg 发会被 SDK 拒绝。"""
    provider, recorder = deepseek_with_recorder()
    await drain(provider, thinking=False)

    assert "thinking" not in recorder.calls[0]  # 不能是顶层参数
    assert recorder.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in recorder.calls[0]


async def test_thinking_none_follows_global_default() -> None:
    provider, recorder = deepseek_with_recorder()
    provider.settings = Settings(deepseek_api_key="test", deepseek_thinking=False)
    await drain(provider, thinking=None)
    assert recorder.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


async def test_selected_deepseek_effort_is_sent_only_while_thinking() -> None:
    from dataclasses import replace

    provider, recorder = deepseek_with_recorder()
    provider.target = replace(provider.target, effort="max")
    await drain(provider, thinking=True)
    assert recorder.calls[0]["reasoning_effort"] == "max"


async def test_complete_uses_the_same_deepseek_thinking_contract() -> None:
    provider, recorder = deepseek_with_recorder()

    with pytest.raises(RuntimeError, match="stop"):
        await provider.complete(system="sys", prompt="hi", thinking=True)
    assert recorder.calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert recorder.calls[0]["reasoning_effort"] == "high"

    with pytest.raises(RuntimeError, match="stop"):
        await provider.complete(system="sys", prompt="hi", thinking=False)
    assert recorder.calls[1]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in recorder.calls[1]


# ---------- Anthropic ----------


async def test_anthropic_thinking_on_uses_adaptive() -> None:
    provider = AnthropicProvider(
        settings=Settings(provider="anthropic", anthropic_api_key="t"),
        client=FakeAnthropic([text_turn("ok")]),
    )
    await drain(provider, thinking=True)
    assert provider.client.messages.calls[0]["thinking"]["type"] == "adaptive"


async def test_anthropic_thinking_off_caps_effort() -> None:
    """Opus 5 上「关闭思考」+ xhigh/max 会 400，必须把 effort 压到 high。"""
    provider = AnthropicProvider(
        settings=Settings(provider="anthropic", anthropic_api_key="t", effort="xhigh"),
        client=FakeAnthropic([text_turn("ok")]),
    )
    await drain(provider, thinking=False)

    call = provider.client.messages.calls[0]
    assert call["thinking"] == {"type": "disabled"}
    assert call["output_config"]["effort"] == "high"


async def test_anthropic_thinking_on_keeps_configured_effort() -> None:
    provider = AnthropicProvider(
        settings=Settings(provider="anthropic", anthropic_api_key="t", effort="xhigh"),
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
    """settings 是 lru_cache 出来的全局单例，覆盖不能就地改它。

    覆盖现在落在 `ModelTarget` 上而不是 Settings 的厂商字段上 —— 这比原来更强：
    settings 根本不参与「调哪个模型」，也就没有被污染的可能。
    """
    from app.llm.factory import get_provider

    base = Settings(provider="deepseek", deepseek_api_key="t", deepseek_model="flash")
    provider = get_provider(base, model_override="pro")

    assert provider.model_name == "pro"
    assert base.deepseek_model == "flash"  # 原对象没被污染


def test_empty_override_keeps_configured_model() -> None:
    from app.llm.factory import get_provider

    base = Settings(provider="deepseek", deepseek_api_key="t", deepseek_model="flash")
    assert get_provider(base, model_override="").model_name == "flash"


def test_protocol_picks_the_implementation_not_the_vendor_name() -> None:
    """注册表按协议分发：加一个 OpenAI 兼容厂商不该需要动 factory。"""
    from app.llm.deepseek_provider import DeepSeekProvider
    from app.llm.factory import get_provider
    from app.llm.target import ModelTarget

    target = ModelTarget(
        protocol="openai_compatible",
        model_id="qwen-max",
        display_name="Qwen",
        base_url="https://example.invalid/v1",
        api_key="k",
        service_slug="some-new-vendor",
    )
    provider = get_provider(Settings(), target=target)

    assert isinstance(provider, DeepSeekProvider)
    assert provider.model_name == "qwen-max"


def test_unknown_protocol_is_rejected_with_the_options() -> None:
    from app.llm.factory import get_provider
    from app.llm.target import ModelTarget

    target = ModelTarget(protocol="gemini", model_id="x", display_name="x")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="未知的模型协议"):
        get_provider(Settings(), target=target)


async def test_no_disable_param_for_a_model_that_cannot_think() -> None:
    """**不思考的模型不需要被关掉思考。**

    `thinking` 是 DeepSeek 的方言，而这条协议下挂着一整类兼容服务。
    硅基流动上的 Qwen3-VL-Instruct 收到它直接 400
    （`current model does not support parameter enable_thinking`），
    一个纯粹多余的参数把整次看图调用打死了。
    """
    from app.llm.target import DEFAULT_CAPABILITIES, ModelTarget

    recorder = RecordingCompletions()
    client = AsyncOpenAI(api_key="test", base_url="http://localhost")
    client.chat.completions = recorder  # type: ignore[assignment]
    target = ModelTarget(
        protocol="openai_compatible",
        model_id="Qwen/Qwen3-VL-30B-A3B-Instruct",
        display_name="Qwen3-VL",
        api_key="test",
        capabilities={**DEFAULT_CAPABILITIES, "vision": True, "thinking": False},
    )
    provider = DeepSeekProvider(settings=Settings(deepseek_api_key="test"), target=target, client=client)

    await drain(provider, thinking=False)
    assert "extra_body" not in recorder.calls[0]


def test_thinking_capability_is_not_the_users_on_off_preference() -> None:
    """能力是「会不会思考」，偏好是「这次要不要思考」，两者一度共用同一个值。

    混在一起会出两种故障：provider 靠能力判断发不发方言参数时，
    「用户关了思考」被读成「模型不会思考」；反过来把能力当偏好用，
    设置页里关掉的思考会自己变回开着。
    """
    from app.llm.target import ModelTarget

    off = ModelTarget.from_settings(
        Settings(provider="deepseek", deepseek_api_key="k", deepseek_thinking=False)
    )
    assert off.capabilities["thinking"] is True
    assert off.thinking_default is False
    assert off.thinking_efforts == ("low", "high", "max")
    assert off.effort == "high"
