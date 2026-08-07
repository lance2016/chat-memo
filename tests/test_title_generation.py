"""标题生成必须又快又便宜：两条路都不许开推理。

标题在 ``done`` 之前被 await，它的耗时会原样变成用户的等待时间 ——
正文早说完了，流还开着、会话锁还占着、输入框还是禁用的。
实测开着思考时一个 16 字的标题要烧 127~542 个思考 token、2.4~21.5 秒，
而标题质量并没有更好，所以「关掉推理」是这条路的硬要求，不是优化偏好。
"""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.service import TITLE_MAX_TOKENS, ChatService
from app.config import Settings
from app.db.models import Conversation
from app.llm.deepseek_provider import DeepSeekProvider
from app.llm.title import TitleClient, get_title_client


class RecordingCompletions:
    """记下 create() 收到的 kwargs，并回一个最小的 OpenAI 形状响应。"""

    def __init__(self, content: str = "记住用 uv 管依赖") -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return _Response(self.content)


class _Response:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)
        self.finish_reason = "stop"


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeOpenAI:
    def __init__(self, completions: RecordingCompletions) -> None:
        self.chat = type("Chat", (), {"completions": completions})()


def settings(**overrides: Any) -> Settings:
    """不读 `.env`。

    这些断言全都依赖「有没有配 key」，而 `Settings()` 默认会加载开发机上的
    `.env` —— 一旦本地配了 `ZHIPU_API_KEY`，测试就会改走真实网络请求，
    结果取决于谁的机器在跑。
    """
    return Settings(_env_file=None, **overrides)


@pytest.fixture
def recorder() -> RecordingCompletions:
    return RecordingCompletions()


async def make_conversation(session: AsyncSession) -> Conversation:
    conversation = Conversation()
    session.add(conversation)
    await session.flush()
    return conversation


# ---------- 智谱这条路 ----------


def test_title_client_only_when_key_configured() -> None:
    """没配 key 就不能启用 —— 否则会拿空 key 去打请求，每轮都失败。"""
    assert get_title_client(settings(zhipu_api_key="")) is None
    assert get_title_client(settings(zhipu_api_key="k", title_model="")) is None
    assert get_title_client(settings(zhipu_api_key="k")) is not None


def test_siliconflow_title_client_only_when_key_configured() -> None:
    assert get_title_client(settings(siliconflow_api_key="")) is None
    assert get_title_client(
        settings(siliconflow_api_key="k", siliconflow_title_model="")
    ) is None
    client = get_title_client(settings(siliconflow_api_key="k"))
    assert client is not None
    assert client.route == "siliconflow/Qwen/Qwen3-8B"


async def test_siliconflow_qwen_title_disables_thinking(
    recorder: RecordingCompletions,
) -> None:
    client = TitleClient(
        settings=settings(
            siliconflow_api_key="k",
            siliconflow_base_url="https://api.siliconflow.cn/v1",
        ),
        client=FakeOpenAI(recorder),
    )

    title = await client.complete(system="sys", prompt="问", max_tokens=500)

    assert title == "记住用 uv 管依赖"
    sent = recorder.calls[0]
    assert sent["model"] == "Qwen/Qwen3-8B"
    assert sent["extra_body"] == {"enable_thinking": False}


def test_siliconflow_takes_priority_over_legacy_zhipu() -> None:
    client = get_title_client(
        settings(siliconflow_api_key="sf", zhipu_api_key="zhipu")
    )
    assert client is not None
    assert client.route == "siliconflow/Qwen/Qwen3-8B"


async def test_zhipu_title_disables_reasoning(
    recorder: RecordingCompletions,
) -> None:
    client = TitleClient(
        settings=settings(zhipu_api_key="k", title_model="glm-4.7-flash"),
        client=FakeOpenAI(recorder),
    )

    title = await client.complete(system="sys", prompt="问", max_tokens=500)

    assert title == "记住用 uv 管依赖"
    sent = recorder.calls[0]
    assert sent["model"] == "glm-4.7-flash"
    # 这是全部的重点：智谱的推理开关不是标准字段，得 extra_body 透传，
    # 而且 GLM 默认开着思考，不显式关掉标题就要白等
    assert sent["extra_body"] == {"thinking": {"type": "disabled"}}


async def test_service_prefers_zhipu_when_configured(
    session: AsyncSession, recorder: RecordingCompletions
) -> None:
    """注入了 title_client 时，标题不该再打到聊天 provider 上。"""
    config = settings(
        anthropic_api_key="test", zhipu_api_key="k", title_model="glm-4.7-flash"
    )

    class RefusingProvider:
        async def complete(self, **_kwargs: Any) -> str:
            raise AssertionError("配了智谱就不该走聊天 provider")

    service = ChatService(
        session,
        provider=RefusingProvider(),
        settings=config,
        title_client=TitleClient(settings=config, client=FakeOpenAI(recorder)),
    )

    assert await service._complete_title("记住：我用 uv 管理依赖") == "记住用 uv 管依赖"
    assert recorder.calls[0]["max_tokens"] == TITLE_MAX_TOKENS


# ---------- 退回聊天 provider 这条路 ----------


async def test_fallback_title_disables_thinking(
    session: AsyncSession, recorder: RecordingCompletions
) -> None:
    """没配智谱时退回聊天 provider，但仍然必须关掉思考。"""
    config = settings(deepseek_api_key="test", zhipu_api_key="")
    provider = DeepSeekProvider(settings=config, client=FakeOpenAI(recorder))
    service = ChatService(session, provider=provider, settings=config)

    assert await service._complete_title("记住：我用 uv 管理依赖") == "记住用 uv 管依赖"
    sent = recorder.calls[0]
    # DeepSeek 不传就是默认开着，所以关的时候必须显式发出来
    assert sent["extra_body"] == {"thinking": {"type": "disabled"}}
    assert sent["max_tokens"] == TITLE_MAX_TOKENS


async def test_consolidation_keeps_thinking(recorder: RecordingCompletions) -> None:
    """整理是质量最敏感、频率最低的活，它的思考不能被顺手关掉。"""
    provider = DeepSeekProvider(
        settings=settings(deepseek_api_key="test"), client=FakeOpenAI(recorder)
    )

    await provider.complete(system="sys", prompt="整理今天的对话")

    assert "extra_body" not in recorder.calls[0]
