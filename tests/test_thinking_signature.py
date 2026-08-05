"""切 provider 时的 thinking 块兼容性。

DeepSeek 生成的 thinking 没有 signature（Anthropic 特有），中断兜底保存的也没有。
同一会话换到 Claude 时，这些块会让请求 400，而且历史里一直留着 —— 会话永久不可用。
"""

from app.config import Settings
from app.llm.anthropic_provider import AnthropicProvider, strip_unsigned_thinking
from tests.fakes import FakeAnthropic, text_turn


def user(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def test_keeps_signed_thinking() -> None:
    """Claude 自己产生的带签名，必须原样保留 —— 丢了会破坏多轮回传。"""
    history = [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "推理", "signature": "sig-abc"},
                {"type": "text", "text": "答案"},
            ],
        }
    ]
    assert strip_unsigned_thinking(history) == history


def test_drops_unsigned_thinking_keeps_the_rest() -> None:
    """DeepSeek 产生的 thinking 没签名，丢掉，但同一条消息里的正文要留下。"""
    out = strip_unsigned_thinking(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "DeepSeek 的推理"},
                    {"type": "text", "text": "答案"},
                ],
            }
        ]
    )
    assert out[0]["content"] == [{"type": "text", "text": "答案"}]


def test_drops_message_that_would_become_empty() -> None:
    """只剩空 content 的消息要整条丢掉 —— API 会拒绝空 content。"""
    out = strip_unsigned_thinking(
        [
            user("问"),
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "只有思考"}]},
            user("再问"),
        ]
    )
    assert [m["role"] for m in out] == ["user", "user"]


def test_preserves_tool_blocks() -> None:
    """工具调用不能被误伤，否则 tool_use/tool_result 配对断裂会 400。"""
    history = [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "无签名"},
                {"type": "tool_use", "id": "c1", "name": "memory", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "ok"}],
        },
    ]
    out = strip_unsigned_thinking(history)
    assert out[0]["content"] == [
        {"type": "tool_use", "id": "c1", "name": "memory", "input": {}}
    ]
    assert out[1] == history[1]


def test_does_not_mutate_input() -> None:
    history = [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "x"},
                {"type": "text", "text": "y"},
            ],
        }
    ]
    strip_unsigned_thinking(history)
    assert len(history[0]["content"]) == 2  # 原对象没被改


def test_string_content_passes_through() -> None:
    assert strip_unsigned_thinking([{"role": "user", "content": "纯字符串"}]) == [
        {"role": "user", "content": "纯字符串"}
    ]


async def test_provider_filters_before_sending() -> None:
    """端到端：provider 发出去的请求体里不该有无签名 thinking。"""
    provider = AnthropicProvider(
        settings=Settings(anthropic_api_key="t"), client=FakeAnthropic([text_turn("ok")])
    )
    history = [
        user("问"),
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "DeepSeek 留下的"},
                {"type": "text", "text": "旧答案"},
            ],
        },
        user("再问"),
    ]
    async for _ in provider.run(system="sys", messages=history):
        pass

    sent = provider.client.messages.calls[0]["messages"]
    blocks = [b for m in sent if isinstance(m["content"], list) for b in m["content"]]
    assert all(b.get("type") != "thinking" for b in blocks)
