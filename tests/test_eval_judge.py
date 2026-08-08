from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.eval.dataset import (
    Correction,
    EvalCase,
    EvalConversation,
    EvalMessage,
    Expectation,
)
from app.eval.judge import Judge, judge_no_op


class FakeProvider:
    """按顺序吐预设字符串。裁判只用到 `complete`，不需要 agent loop。"""

    model_name = "fake-judge"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def run(self, **kwargs: Any) -> AsyncIterator[Any]:  # pragma: no cover - 用不到
        raise NotImplementedError("裁判不跑 agent loop")

    async def complete(
        self, *, system: str, prompt: str, max_tokens: int | None = None,
        thinking: bool = True,
    ) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else ""


def make_case(
    facts: list[str] | None = None,
    corrections: list[Correction] | None = None,
    forbidden: list[str] | None = None,
    no_op: bool = False,
) -> EvalCase:
    return EvalCase(
        id="c1",
        date="2026-08-08",
        memory_before={"/memories/MEMORY.md": "- 用户用 pip 管理依赖"},
        conversations=[
            EvalConversation(
                title="会话", messages=[EvalMessage(role="user", text="我改用 uv 了")]
            )
        ],
        expect=Expectation(
            facts=facts or [],
            corrections=corrections or [],
            forbidden=forbidden or [],
            no_op=no_op,
        ),
    )


async def test_parses_all_three_metrics_in_one_call() -> None:
    """一次调用判完三个指标，且判定按 id 回填到标注上——不是按模型复述的文本。"""
    case = make_case(
        facts=["用户改用 uv 管理依赖", "用户在做一个聊天项目"],
        corrections=[Correction(stale="用户用 pip 管理依赖", becomes="改用 uv")],
    )
    reply = json.dumps(
        {
            # 故意乱序 + 复述文本，验证回填只认 id
            "facts": [
                {"id": 1, "recalled": False, "evidence": "记忆里没提项目"},
                {"id": 0, "recalled": True, "evidence": "- 用 uv 管理 Python 依赖"},
            ],
            "corrections": [
                {"id": 0, "status": "fixed", "evidence": "pip 那行已被替换"}
            ],
            "errors": [
                {"text": "用户住在示例市", "kind": "fabricated", "evidence": "对话里没提"}
            ],
        },
        ensure_ascii=False,
    )
    provider = FakeProvider([f"好的，这是判定：\n```json\n{reply}\n```"])

    verdict = await Judge(provider).judge(case, {"/memories/MEMORY.md": "- 用 uv"}, "原文")

    assert not verdict.failed
    assert [f.fact for f in verdict.facts] == [
        "用户改用 uv 管理依赖",
        "用户在做一个聊天项目",
    ]
    assert [f.recalled for f in verdict.facts] == [True, False]
    assert verdict.recall == 0.5
    assert verdict.corrections[0].stale == "用户用 pip 管理依赖"
    assert verdict.correction_rate == 1.0
    assert verdict.error_count == 1
    assert verdict.errors[0].kind == "fabricated"


async def test_prompt_hedges_length_bias_and_scope() -> None:
    """长度偏好和「不评文笔」必须在 prompt 里显式写死，去掉它们评的就是另一件事。"""
    case = make_case(facts=["用户改用 uv"])
    provider = FakeProvider(['{"facts": [{"id": 0, "recalled": true, "evidence": "x"}]}'])
    judge = Judge(provider)

    await judge.judge(case, {"/memories/MEMORY.md": "- uv"}, "原文")

    from app.eval.judge import JUDGE_SYSTEM

    assert "简洁不扣分" in JUDGE_SYSTEM
    assert "不是在评价文笔" in JUDGE_SYSTEM
    # 对话原文必须进 prompt，否则「编造」判不了
    assert "原文" in provider.prompts[0]


async def test_unparsable_output_retries_once_then_fails() -> None:
    """连续两次解析失败要 failed=True，绝不能当成 0 分——那会让抽风看起来像质量下降。"""
    case = make_case(facts=["用户改用 uv"])
    provider = FakeProvider(["这不是 JSON", "还是不是 JSON"])

    verdict = await Judge(provider).judge(case, {"/m": "内容"}, "原文")

    assert verdict.failed
    assert verdict.recall is None
    assert verdict.correction_rate is None
    assert len(provider.prompts) == 2  # 重试一次，不是无限重试


async def test_incomplete_coverage_is_retried() -> None:
    """漏判一条事实算裁判没跑成——默默补成 false 就是把裁判失误算成被测系统失分。"""
    case = make_case(facts=["事实一", "事实二"])
    complete = json.dumps(
        {
            "facts": [
                {"id": 0, "recalled": True, "evidence": "a"},
                {"id": 1, "recalled": True, "evidence": "b"},
            ]
        }
    )
    provider = FakeProvider(['{"facts": [{"id": 0, "recalled": true}]}', complete])

    verdict = await Judge(provider).judge(case, {"/m": "内容"}, "原文")

    assert not verdict.failed
    assert verdict.recall == 1.0
    assert len(provider.prompts) == 2


async def test_unknown_correction_status_counts_as_missed() -> None:
    """认不出的状态不给分：能确认「改好了」才算 fixed。"""
    case = make_case(corrections=[Correction(stale="用户用 pip 管理依赖")])
    provider = FakeProvider(
        ['{"corrections": [{"id": 0, "status": "部分修正", "evidence": "e"}]}']
    )

    verdict = await Judge(provider).judge(case, {"/m": "内容"}, "原文")

    assert verdict.corrections[0].status == "missed"
    assert verdict.correction_rate == 0.0


async def test_coexist_is_not_counted_as_fixed() -> None:
    """新旧并存是最糟的失败，不能拿部分分。"""
    case = make_case(
        corrections=[Correction(stale="用户用 pip 管理依赖"), Correction(stale="用户用 pip 管理依赖")]
    )
    provider = FakeProvider(
        [
            json.dumps(
                {
                    "corrections": [
                        {"id": 0, "status": "coexist", "evidence": "两句都在"},
                        {"id": 1, "status": "fixed", "evidence": "换掉了"},
                    ]
                }
            )
        ]
    )

    verdict = await Judge(provider).judge(case, {"/m": "内容"}, "原文")

    assert verdict.correction_rate == 0.5


async def test_empty_expectations_return_none_not_zero() -> None:
    """没标事实/修正时指标是 None——返回 0 会把 no_op 样本算进平均分往下拉。"""
    case = make_case(no_op=True)
    provider = FakeProvider([])

    verdict = await Judge(provider).judge(case, {}, "原文")

    assert verdict.recall is None
    assert verdict.correction_rate is None
    assert verdict.error_count == 0
    assert not verdict.failed
    assert provider.prompts == []  # 没有可判定的标注就不该花 token


def test_judge_no_op_returns_none_for_normal_case() -> None:
    """不是 no_op 样本，这个判定不适用——返回 None 而不是 True/False。"""
    assert judge_no_op(make_case(facts=["某个事实"]), []) is None


def test_judge_no_op_passes_when_nothing_written() -> None:
    assert judge_no_op(make_case(no_op=True), []) is True


def test_judge_no_op_fails_when_memory_was_written() -> None:
    assert judge_no_op(make_case(no_op=True), ["/memories/profile/basics.md"]) is False
