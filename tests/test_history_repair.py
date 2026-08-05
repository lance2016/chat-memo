"""中断的对话必须能继续用。

点停止、关标签页、断网、开发时热重载，都会留下「模型发起了工具调用但结果没落库」的历史。
两家 API 都要求 tool_use 和 tool_result 严格配对，不修的话会话之后每条消息都 400。
"""

import datetime as dt

from app.chat.service import INTERRUPTED_RESULT, build_runtime_context, sanitize_history
from app.config import Settings
from app.llm.deepseek_provider import to_openai_messages


def assistant_tool_use(*ids: str) -> dict:
    return {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": i, "name": "memory", "input": {"command": "view"}}
            for i in ids
        ],
    }


def tool_results(*ids: str) -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": i, "content": "ok"} for i in ids
        ],
    }


def user_text(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def assistant_text(text: str) -> dict:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


# ---------- 不该动的情况 ----------


def test_plain_conversation_untouched() -> None:
    history = [user_text("你好"), assistant_text("嗨")]
    assert sanitize_history(history) == history


def test_complete_tool_round_untouched() -> None:
    history = [user_text("记一下"), assistant_tool_use("c1"), tool_results("c1")]
    assert sanitize_history(history) == history


def test_multiple_complete_tool_calls_untouched() -> None:
    history = [
        user_text("记一下"),
        assistant_tool_use("c1", "c2"),
        tool_results("c1", "c2"),
        assistant_text("好了"),
    ]
    assert sanitize_history(history) == history


# ---------- 需要修的情况 ----------


def test_orphan_at_end_gets_error_result() -> None:
    """最常见：模型刚发起调用就被打断，后面什么都没有。"""
    repaired = sanitize_history([user_text("记一下"), assistant_tool_use("c1")])

    assert len(repaired) == 3
    block = repaired[-1]["content"][0]
    assert block["tool_use_id"] == "c1"
    assert block["is_error"] is True
    assert block["content"] == INTERRUPTED_RESULT


def test_partially_answered_tool_calls_are_completed() -> None:
    """两个调用只回来一个结果：补上缺的那个，保留已有的。"""
    repaired = sanitize_history(
        [user_text("记一下"), assistant_tool_use("c1", "c2"), tool_results("c1")]
    )

    results = repaired[-1]["content"]
    assert [b["tool_use_id"] for b in results] == ["c1", "c2"]
    assert results[0].get("is_error") is None  # 原有的不动
    assert results[1]["is_error"] is True


def test_orphan_followed_by_new_user_message() -> None:
    """被打断后用户又发了新消息 —— 补的结果要插在中间，不能顶掉用户的话。"""
    repaired = sanitize_history(
        [user_text("记一下"), assistant_tool_use("c1"), user_text("继续")]
    )

    assert [m["role"] for m in repaired] == ["user", "assistant", "user", "user"]
    assert repaired[2]["content"][0]["tool_use_id"] == "c1"
    assert repaired[3]["content"][0]["text"] == "继续"


def test_repaired_history_passes_openai_pairing() -> None:
    """真正的验收标准：翻译成 OpenAI 格式后每个 tool_call 都有对应的 tool 消息。

    这正是之前 400 的那条规则。
    """
    broken = [user_text("记一下"), assistant_tool_use("c1", "c2"), user_text("继续")]
    converted = to_openai_messages(sanitize_history(broken))

    called = {
        c["id"] for m in converted if m.get("tool_calls") for c in m["tool_calls"]
    }
    answered = {m["tool_call_id"] for m in converted if m["role"] == "tool"}
    assert called == answered == {"c1", "c2"}


def test_multiple_interrupted_rounds() -> None:
    history = [
        user_text("一"),
        assistant_tool_use("c1"),
        user_text("二"),
        assistant_tool_use("c2"),
    ]
    repaired = sanitize_history(history)
    answered = {
        b["tool_use_id"]
        for m in repaired
        for b in m["content"]
        if b.get("type") == "tool_result"
    }
    assert answered == {"c1", "c2"}


# ---------- 运行时上下文 ----------


def test_runtime_context_has_date_and_model() -> None:
    settings = Settings(provider="deepseek", deepseek_model="deepseek-v4-flash")
    text = build_runtime_context(settings, dt.datetime(2026, 8, 6, 14, 30))

    assert "2026-08-06" in text
    assert "星期四" in text
    assert "14:30" in text
    # 身份要说清楚，否则模型会瞎猜成 Claude
    assert "deepseek-v4-flash" in text


def test_runtime_context_reports_active_provider() -> None:
    anthropic = build_runtime_context(
        Settings(provider="anthropic", model="claude-opus-5"), dt.datetime(2026, 8, 6)
    )
    assert "claude-opus-5" in anthropic
    assert "deepseek" not in anthropic
