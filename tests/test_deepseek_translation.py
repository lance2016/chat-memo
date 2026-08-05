"""标准格式（Anthropic content blocks）↔ OpenAI 消息 的互转测试。

这层翻译是换 provider 后历史对话还能用的前提，出错的表现是模型突然「失忆」或直接 400。
"""

import json

from app.llm.deepseek_provider import to_content_blocks, to_openai_messages


def test_user_text_message() -> None:
    out = to_openai_messages(
        [{"role": "user", "content": [{"type": "text", "text": "你好"}]}]
    )
    assert out == [{"role": "user", "content": "你好"}]


def test_assistant_text_only() -> None:
    out = to_openai_messages(
        [{"role": "assistant", "content": [{"type": "text", "text": "答案"}]}]
    )
    assert out == [{"role": "assistant", "content": "答案"}]


def test_thinking_blocks_are_dropped() -> None:
    """DeepSeek 不接受 reasoning_content 回传，必须丢掉。"""
    out = to_openai_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "内部推理", "signature": "s"},
                    {"type": "text", "text": "答案"},
                ],
            }
        ]
    )
    assert out == [{"role": "assistant", "content": "答案"}]
    assert "内部推理" not in json.dumps(out, ensure_ascii=False)


def test_tool_use_becomes_tool_calls() -> None:
    out = to_openai_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "我记一下"},
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "memory",
                        "input": {"command": "view", "path": "/memories"},
                    },
                ],
            }
        ]
    )
    assert out[0]["content"] == "我记一下"
    call = out[0]["tool_calls"][0]
    assert call["id"] == "call_1"
    assert call["function"]["name"] == "memory"
    assert json.loads(call["function"]["arguments"])["command"] == "view"


def test_tool_only_assistant_has_null_content() -> None:
    """只有工具调用没有正文时，content 要是 None 而不是空串。"""
    out = to_openai_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "c1", "name": "memory", "input": {}}
                ],
            }
        ]
    )
    assert out[0]["content"] is None
    assert len(out[0]["tool_calls"]) == 1


def test_tool_results_become_separate_tool_messages() -> None:
    """一条 user 消息里的多个 tool_result 要拆成多条 tool 消息。"""
    out = to_openai_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "c1", "content": "结果一"},
                    {"type": "tool_result", "tool_use_id": "c2", "content": "结果二"},
                ],
            }
        ]
    )
    assert out == [
        {"role": "tool", "tool_call_id": "c1", "content": "结果一"},
        {"role": "tool", "tool_call_id": "c2", "content": "结果二"},
    ]


def test_full_tool_round_trip_preserves_call_id_pairing() -> None:
    """tool_calls 的 id 必须和后续 tool 消息的 tool_call_id 对得上，否则 400。"""
    history = [
        {"role": "user", "content": [{"type": "text", "text": "记一下"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call_x", "name": "memory", "input": {"command": "view"}}
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_x", "content": "空的"}
            ],
        },
    ]
    out = to_openai_messages(history)
    assert [m["role"] for m in out] == ["user", "assistant", "tool"]
    assert out[1]["tool_calls"][0]["id"] == out[2]["tool_call_id"] == "call_x"


def test_tool_result_with_block_list_content() -> None:
    out = to_openai_messages(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "c1",
                        "content": [{"type": "text", "text": "块形式的结果"}],
                    }
                ],
            }
        ]
    )
    assert out[0]["content"] == "块形式的结果"


def test_plain_string_content_passes_through() -> None:
    out = to_openai_messages([{"role": "user", "content": "直接是字符串"}])
    assert out == [{"role": "user", "content": "直接是字符串"}]


def test_to_content_blocks_shapes_storage_format() -> None:
    blocks = to_content_blocks(
        "答案",
        "推理过程",
        [{"id": "c1", "name": "memory", "arguments": '{"command":"view"}'}],
    )
    assert blocks[0] == {"type": "thinking", "thinking": "推理过程"}
    assert blocks[1] == {"type": "text", "text": "答案"}
    assert blocks[2] == {
        "type": "tool_use",
        "id": "c1",
        "name": "memory",
        "input": {"command": "view"},
    }


def test_to_content_blocks_skips_empty_parts() -> None:
    assert to_content_blocks("", "", []) == []
    assert to_content_blocks("只有文本", "", []) == [
        {"type": "text", "text": "只有文本"}
    ]


def test_round_trip_survives_translation() -> None:
    """出去再回来，结构不能变形。"""
    original = to_content_blocks(
        "好的", "", [{"id": "c1", "name": "memory", "arguments": '{"command":"view"}'}]
    )
    history = [
        {"role": "assistant", "content": original},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "ok"}],
        },
    ]
    out = to_openai_messages(history)
    assert out[0]["tool_calls"][0]["function"]["name"] == "memory"
    assert out[1]["tool_call_id"] == "c1"
