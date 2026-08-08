"""重放与指标汇总。

这些用例钉住的是**评测本身的可信度**。评测代码错了不会有任何症状 —— 它照样输出
一个看起来很像那么回事的分数，然后你拿着那个分数去改提示词。所以起始状态隔离、
「不适用」和「零分」的区分、噪声兜底这几条必须有测试。
"""

import datetime as dt

from app.config import Settings
from app.eval.dataset import (
    Correction,
    EvalCase,
    EvalConversation,
    EvalMessage,
    Expectation,
)
from app.eval.judge import CorrectionVerdict, FactVerdict, JudgeVerdict
from app.eval.metrics import (
    MIN_NOISE,
    compare,
    measure_noise,
    score_case,
    summarize,
)
from app.eval.runner import diff_memory, run_case
from app.llm.anthropic_provider import AnthropicProvider
from tests.fakes import FakeAnthropic, text_turn, tool_turn

TODAY = dt.date.today()


def provider_with(turns: list) -> AnthropicProvider:
    return AnthropicProvider(
        settings=Settings(anthropic_api_key="test"), client=FakeAnthropic(turns)
    )


def make_case(**overrides) -> EvalCase:
    defaults = dict(
        id="case-1",
        date=TODAY.isoformat(),
        memory_before={"/memories/MEMORY.md": "# 记忆索引"},
        conversations=[
            EvalConversation(
                title="测试会话",
                messages=[
                    EvalMessage(role="user", text="我现在用 uv 管理依赖"),
                    EvalMessage(role="assistant", text="记住了"),
                ],
            )
        ],
        expect=Expectation(facts=["用户用 uv 管理 Python 依赖"]),
    )
    return EvalCase(**{**defaults, **overrides})


# ---------- 重放 ----------


async def test_replay_produces_a_diff_against_the_frozen_snapshot() -> None:
    """整理前的状态必须严格等于 memory_before —— 那是可复现的全部前提。"""
    case = make_case()
    provider = provider_with(
        [
            text_turn("用户用 uv 管理 Python 依赖"),
            tool_turn(
                "memory",
                {
                    "command": "create",
                    "path": "/memories/profile/preferences.md",
                    "file_text": "- 用 uv 管理 Python 依赖",
                },
            ),
            text_turn("已整理"),
        ]
    )

    run = await run_case(case, provider)

    assert not run.crashed
    assert run.diff.created == ("/memories/profile/preferences.md",)
    assert "uv" in run.memory_after["/memories/profile/preferences.md"]
    # 种子记忆没被算成模型的产出
    assert run.result is not None and run.result.memory_writes == 1


async def test_cases_do_not_leak_into_each_other() -> None:
    """每条样本一个一次性库。漏一点上一条的记忆，后面的判分全是错的。"""
    first = make_case(id="first")
    provider = provider_with(
        [
            text_turn("用户用 uv"),
            tool_turn(
                "memory",
                {"command": "create", "path": "/memories/a.md", "file_text": "第一条"},
            ),
            text_turn("好了"),
        ]
    )
    await run_case(first, provider)

    second = make_case(id="second")
    run = await run_case(second, provider_with([text_turn("无事"), text_turn("无需改动")]))

    assert "/memories/a.md" not in run.memory_after


async def test_seeded_memory_is_visible_to_the_model() -> None:
    """整理要能读到 memory_before，否则「和旧记录冲突就改掉」这类样本根本测不了。"""
    case = make_case(
        memory_before={
            "/memories/MEMORY.md": "- [偏好](profile/preferences.md) — 工具偏好",
            "/memories/profile/preferences.md": "- 用 pip 管理依赖",
        },
        expect=Expectation(corrections=[Correction(stale="用 pip 管理依赖")]),
    )
    provider = provider_with(
        [
            text_turn("用户改用 uv"),
            tool_turn(
                "memory", {"command": "view", "path": "/memories/profile/preferences.md"}
            ),
            tool_turn(
                "memory",
                {
                    "command": "str_replace",
                    "path": "/memories/profile/preferences.md",
                    "old_str": "用 pip 管理依赖",
                    "new_str": "用 uv 管理依赖",
                },
            ),
            text_turn("已修正"),
        ]
    )

    run = await run_case(case, provider)

    assert run.diff.modified == ("/memories/profile/preferences.md",)
    assert "pip" not in run.memory_after["/memories/profile/preferences.md"]


async def test_dropped_input_is_unusable_not_a_low_score() -> None:
    """摘要失败时模型压根没看到那段对话。

    生产代码会跳过失败的会话继续跑（对的），但评测必须把它升级成「本条不可用」——
    否则「输入被吃掉」会伪装成「召回低」，而 no_op 样本还会因此判成满分。
    """
    # 预设轮次不够，摘要那步会抛异常，被 Consolidator 记成 failed_summaries
    run = await run_case(make_case(), provider_with([]))

    assert run.crashed
    assert "输入不完整" in run.detail
    assert run.diff.is_empty


async def test_no_op_case_with_dropped_input_never_scores_as_a_pass() -> None:
    """最危险的组合：什么都没写看起来像「正确地保持了沉默」。"""
    case = make_case(expect=Expectation(no_op=True))

    run = await run_case(case, provider_with([]))
    score = score_case(case, run, None)

    assert run.diff.is_empty  # 确实什么都没写
    assert not score.usable  # 但这条不能算数
    assert summarize([score]).no_op_respected is None


async def test_audit_runs_on_the_replayed_state() -> None:
    """第 0 层指标在重放结果上照跑 —— 写了记忆没更索引，评测要抓得到。"""
    provider = provider_with(
        [
            text_turn("用户用 uv"),
            tool_turn(
                "memory",
                {
                    "command": "create",
                    "path": "/memories/profile/preferences.md",
                    "file_text": "- uv",
                },
            ),
            text_turn("已整理"),
        ]
    )

    run = await run_case(make_case(), provider)

    assert run.audit.missing == ("/memories/profile/preferences.md",)


# ---------- diff ----------


def test_rewriting_identical_content_is_not_a_modification() -> None:
    """「改了但改回原样」不该算活动量，否则写入次数会虚高。"""
    diff = diff_memory({"/memories/a.md": "同样的内容"}, {"/memories/a.md": "同样的内容"})

    assert diff.is_empty


def test_diff_separates_create_modify_delete() -> None:
    diff = diff_memory(
        {"/memories/a.md": "旧", "/memories/gone.md": "要删"},
        {"/memories/a.md": "新", "/memories/b.md": "新建"},
    )

    assert diff.created == ("/memories/b.md",)
    assert diff.modified == ("/memories/a.md",)
    assert diff.deleted == ("/memories/gone.md",)


# ---------- 打分 ----------


def test_not_applicable_is_not_zero() -> None:
    """no_op 样本没有 facts 要召回。记成 0 会把它算进平均分，看起来像质量下降。"""
    case = make_case(expect=Expectation(no_op=True))
    verdict = JudgeVerdict()

    score = score_case(case, _clean_run(), verdict)

    assert score.recall is None
    assert summarize([score]).recall is None


def test_judge_failure_keeps_the_mechanical_metrics() -> None:
    """裁判挂了不影响代码算出来的指标，整条丢掉等于白烧这次运行的 token。"""
    run = _clean_run()
    run.result.memory_writes = 3

    score = score_case(make_case(), run, JudgeVerdict(failed=True, detail="解析失败"))

    assert score.judge_failed and not score.usable
    assert score.memory_writes == 3
    assert summarize([score]).writes_total == 3


def test_no_op_violation_is_detected() -> None:
    case = make_case(expect=Expectation(no_op=True))
    run = _clean_run()
    run.diff = diff_memory({}, {"/memories/a.md": "不该写的东西"})

    score = score_case(case, run, None)

    assert score.no_op_respected is False
    assert summarize([score]).no_op_respected == 0.0


def test_summary_keeps_numerator_and_denominator() -> None:
    """只给比例的报告没法判断可信度：3 条里对 2 条和 30 条里对 20 条不是一回事。"""
    good = score_case(
        make_case(),
        _clean_run(),
        JudgeVerdict(facts=(FactVerdict("a", True, "有"),)),
    )
    crashed = score_case(make_case(id="x"), _crashed_run(), None)

    summary = summarize([good, crashed])

    assert summary.total == 2 and summary.usable == 1 and summary.crashed == 1


def test_correction_coexist_gets_no_partial_credit() -> None:
    """新旧并存是最糟的失败，给部分分等于奖励它。"""
    verdict = JudgeVerdict(
        corrections=(
            CorrectionVerdict("旧的", "fixed", ""),
            CorrectionVerdict("另一条", "coexist", ""),
        )
    )

    assert verdict.correction_rate == 0.5


# ---------- 噪声与对比 ----------


def test_noise_has_a_floor() -> None:
    """几次恰好撞在一起不代表系统稳定，别让它把下限压到 0。"""
    noise = measure_noise("召回", [0.8, 0.8, 0.8])

    assert noise.spread == MIN_NOISE


def test_difference_smaller_than_noise_is_indistinguishable() -> None:
    """评测的价值在于挡住自我安慰。见到正数就说变好的比较器毫无意义。"""
    result = compare("事实召回", 0.80, 0.83, noise=0.05)

    assert result.verdict == "indistinguishable"
    assert "无法区分" in result.render()


def test_difference_beyond_noise_is_called() -> None:
    assert compare("事实召回", 0.70, 0.90, noise=0.05).verdict == "better"
    assert compare("事实召回", 0.90, 0.70, noise=0.05).verdict == "worse"


def test_lower_is_better_metrics_flip() -> None:
    """错误数越少越好，别把「错误变多」报成变好。"""
    result = compare("引入错误", 2.0, 5.0, noise=1.0, higher_is_better=False)

    assert result.verdict == "worse"


def test_missing_baseline_is_unavailable_not_zero() -> None:
    result = compare("事实召回", None, 0.9, noise=0.05)

    assert result.verdict == "unavailable"
    assert "数据不足" in result.render()


# ---------- 夹具 ----------


def _clean_run():
    from app.jobs.consolidate import ConsolidationResult
    from app.eval.runner import CaseRun

    return CaseRun(
        case_id="case-1",
        result=ConsolidationResult(
            date=TODAY.isoformat(), summarized_conversations=1, tool_calls=1
        ),
    )


def _crashed_run():
    from app.eval.runner import CaseRun

    return CaseRun(case_id="x", crashed=True, detail="boom")


# ---------- 端到端 ----------


async def test_pipeline_produces_a_report_and_a_reusable_baseline(tmp_path) -> None:
    """跑一轮 → 出报告 → 存盘 → 下一轮拿它当 baseline。

    这条链路断在任何一环，评测都还是会打印出一个看起来很像那么回事的分数，
    所以整条串起来测一次。
    """
    from app.eval import report
    from app.eval.judge import Judge

    case = make_case()
    provider = provider_with(
        [
            text_turn("用户用 uv 管理 Python 依赖"),
            tool_turn(
                "memory",
                {
                    "command": "create",
                    "path": "/memories/profile/preferences.md",
                    "file_text": "- 用 uv 管理 Python 依赖",
                },
            ),
            text_turn("已整理"),
        ]
    )
    run = await run_case(case, provider)
    verdict = JudgeVerdict(facts=(FactVerdict("用户用 uv 管理 Python 依赖", True, "有"),))
    score = score_case(case, run, verdict)
    summary = summarize([score])

    assert "事实召回" in report.render_summary(summary)
    assert case.id in report.render_cases([score])
    # 索引没更新，这条要被点名
    assert case.id in report.render_attention([score], {case.id: run})

    saved = report.save_run(
        summary, [score], {case.id: run}, {case.id: verdict},
        meta={"model": "fake"}, directory=tmp_path,
    )
    assert report.latest_run(tmp_path) == saved

    baseline = report.load_summary(saved)
    assert baseline.recall == summary.recall
    assert compare("事实召回", baseline.recall, summary.recall, 0.05).verdict == (
        "indistinguishable"
    )
    assert isinstance(Judge(provider), Judge)


def test_attention_stays_quiet_when_everything_passes() -> None:
    """没问题时不要制造噪音 —— 一份每次都在报警的报告等于没有报警。"""
    from app.eval import report
    from app.eval.runner import CaseRun

    score = score_case(
        make_case(),
        CaseRun(case_id="case-1"),
        JudgeVerdict(facts=(FactVerdict("a", True, "有"),)),
    )

    assert "没有需要关注的问题" in report.render_attention([score], {})


# ---------- 配置来源 ----------


async def test_eval_reads_the_effective_settings_not_the_env_snapshot(monkeypatch) -> None:
    """评测必须读「数据库覆盖叠加在 .env 之上」的生效配置。

    用 `get_settings()`（启动快照）的话，在设置页把 provider 换掉之后，评测会去评
    一个你根本没在跑的配置 —— 结果看着像那么回事，偏了却没有任何症状，
    正是这套评测反复要挡的那种失败。
    """
    from app.eval import cli

    async def fake_resolve(_session):
        return Settings(provider="anthropic", model="claude-from-db")

    monkeypatch.setattr(cli, "resolve_settings", fake_resolve)

    settings = await cli._effective_settings()

    assert settings.provider == "anthropic"
    assert settings.model == "claude-from-db"


async def test_settings_fall_back_to_env_when_the_database_is_down(monkeypatch) -> None:
    """没起 compose 也要能跑评测，但不能默默用一份可能过时的配置。"""
    from app.eval import cli

    async def boom(_session):
        raise ConnectionError("数据库连不上")

    monkeypatch.setattr(cli, "resolve_settings", boom)

    settings = await cli._effective_settings()

    assert settings.provider == Settings().provider


def test_credential_hint_shows_which_key_is_in_use() -> None:
    """宿主机上 shell 的同名变量会盖掉 .env，症状是「聊天好好的，评测 401」。

    尾号打出来，和 .env 一比就知道被盖了 —— 否则这个坑要查很久。
    """
    from app.eval import cli

    hint = cli._credential_hint(Settings(provider="deepseek", deepseek_api_key="sk-xxxx5493"))

    assert "5493" in hint and "deepseek" in hint
    assert "没有配置" in cli._credential_hint(Settings(provider="deepseek", deepseek_api_key=""))
