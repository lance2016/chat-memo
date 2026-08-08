"""把一次评测的原始产出汇总成能对照着看的数字。

**这个模块刻意不给总分。** 把召回、错误数、写入量压成一个数字，需要一组权重，
而那组权重是拍脑袋定的 —— 之后所有的「变好了 0.03」都建立在那次拍脑袋上。
指标分开列，趋势自己看，是更诚实也更有用的做法。

第二条：**任何比较都要先过噪声**。同一份输入连跑几次分数本来就会抖，
比抖动还小的差异不能解读成改进。`Comparison.verdict` 干的就是这件事 ——
它默认给出「无法区分」，而不是默认给出「变好了」。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from app.eval.dataset import EvalCase
from app.eval.judge import JudgeVerdict, judge_no_op
from app.eval.runner import CaseRun

# 噪声的兜底下限。样本少的时候标准差可能算出个很小的数，
# 那不代表系统真的稳定，只代表几次恰好撞在一起。
MIN_NOISE = 0.05


@dataclass
class CaseScore:
    """一条样本一次运行的完整判定。字段全是 optional 是有意的 —— 见 `recall`。"""

    case_id: str
    # None = 这条样本不适用这个指标（比如 no_op 样本没有 facts 要召回），
    # 和 0.0（适用但一条都没召回）是两回事，不能混。混了会把「不适用」算进平均分。
    recall: float | None = None
    correction_rate: float | None = None
    error_count: int = 0
    # no_op 样本专用：True = 正确地什么都没做
    no_op_respected: bool | None = None
    # 第 0 层：机械指标
    index_issues: int = 0
    # 第 1 层：过程指标
    memory_writes: int = 0
    tool_calls: int = 0
    seconds: float = 0.0
    # 这两个都表示「这次结果不可用」，但原因不同，报告里要分开显示
    crashed: bool = False
    judge_failed: bool = False
    detail: str = ""

    @property
    def usable(self) -> bool:
        return not self.crashed and not self.judge_failed


def score_case(case: EvalCase, run: CaseRun, verdict: JudgeVerdict | None) -> CaseScore:
    """把重放结果和裁判意见合成一条打分。

    裁判失败时保留第 0/1 层的数字 —— 那些是代码算的，不受裁判影响，
    整条丢掉等于白烧了这次运行的 token。
    """
    score = CaseScore(
        case_id=case.id,
        no_op_respected=judge_no_op(case, list(run.diff.changed)),
        index_issues=run.audit.issue_count,
        memory_writes=run.result.memory_writes if run.result else 0,
        tool_calls=run.result.tool_calls if run.result else 0,
        seconds=run.seconds,
        crashed=run.crashed,
        detail=run.detail,
    )
    if verdict is None:
        return score
    if verdict.failed:
        score.judge_failed = True
        score.detail = score.detail or verdict.detail
        return score

    score.recall = verdict.recall
    score.correction_rate = verdict.correction_rate
    score.error_count = verdict.error_count
    return score


@dataclass
class Summary:
    """一轮评测的汇总。分子分母都留着 —— 只给比例的报告没法判断可信度。"""

    total: int = 0
    usable: int = 0
    crashed: int = 0
    judge_failed: int = 0
    recall: float | None = None
    correction_rate: float | None = None
    # 每条样本平均引入几个错误。这个数不该被平均掉细节，所以另存总数
    errors_total: int = 0
    # no_op 样本里正确保持沉默的比例
    no_op_respected: float | None = None
    # 第 0 层。整套指标里唯一确定无误的那个
    index_issues_total: int = 0
    index_clean_rate: float | None = None
    # 第 1 层
    writes_total: int = 0
    tool_calls_total: int = 0
    seconds_total: float = 0.0

    def render(self) -> list[tuple[str, str]]:
        """给终端表格用的 (指标, 值)。顺序即重要性：确定的在前，带噪声的在后。"""
        rows = [
            ("样本", f"{self.usable}/{self.total} 可用"),
            ("索引干净率", _pct(self.index_clean_rate)),
            ("索引问题总数", str(self.index_issues_total)),
            ("事实召回", _pct(self.recall)),
            ("修正正确率", _pct(self.correction_rate)),
            ("引入错误", f"{self.errors_total} 条"),
            ("no_op 遵守率", _pct(self.no_op_respected)),
            ("记忆写入", f"{self.writes_total} 次"),
            ("工具调用", f"{self.tool_calls_total} 次"),
            ("耗时", f"{self.seconds_total:.1f}s"),
        ]
        if self.crashed:
            rows.append(("执行崩溃", f"{self.crashed} 条"))
        if self.judge_failed:
            rows.append(("裁判失败", f"{self.judge_failed} 条"))
        return rows


def summarize(scores: list[CaseScore]) -> Summary:
    usable = [s for s in scores if s.usable]
    return Summary(
        total=len(scores),
        usable=len(usable),
        crashed=sum(1 for s in scores if s.crashed),
        judge_failed=sum(1 for s in scores if s.judge_failed),
        recall=_mean([s.recall for s in usable]),
        correction_rate=_mean([s.correction_rate for s in usable]),
        errors_total=sum(s.error_count for s in usable),
        no_op_respected=_mean(
            [
                1.0 if s.no_op_respected else 0.0
                for s in usable
                if s.no_op_respected is not None
            ]
        ),
        # 索引指标不看 usable：它是纯代码算的，裁判失败不影响它的有效性。
        index_issues_total=sum(s.index_issues for s in scores if not s.crashed),
        index_clean_rate=_mean(
            [1.0 if s.index_issues == 0 else 0.0 for s in scores if not s.crashed]
        ),
        writes_total=sum(s.memory_writes for s in scores),
        tool_calls_total=sum(s.tool_calls for s in scores),
        seconds_total=sum(s.seconds for s in scores),
    )


@dataclass
class Noise:
    """同一份输入重复跑出来的波动。这是所有对比的解释力下限。"""

    metric: str
    values: list[float] = field(default_factory=list)
    spread: float = 0.0

    def render(self) -> str:
        shown = "、".join(f"{v:.2f}" for v in self.values)
        return f"{self.metric}：{shown}（波动 ±{self.spread:.2f}）"


def measure_noise(metric: str, values: list[float | None]) -> Noise:
    """同一配置重复跑 N 次的波动幅度。

    用总体标准差而不是极差：极差只由最极端的两次决定，跑得越多它越大，
    那会得出「跑得越多系统越不稳定」这种荒谬结论。
    """
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return Noise(metric=metric, values=clean, spread=MIN_NOISE)
    return Noise(
        metric=metric, values=clean, spread=max(statistics.pstdev(clean), MIN_NOISE)
    )


@dataclass(frozen=True)
class Comparison:
    metric: str
    baseline: float | None
    candidate: float | None
    noise: float
    verdict: str  # better | worse | indistinguishable | unavailable

    def render(self) -> str:
        if self.verdict == "unavailable":
            return f"{self.metric}：数据不足，无法比较"
        delta = (self.candidate or 0) - (self.baseline or 0)
        label = {
            "better": "变好",
            "worse": "变差",
            "indistinguishable": "无法区分（差异小于噪声）",
        }[self.verdict]
        return (
            f"{self.metric}：{self.baseline:.2f} → {self.candidate:.2f} "
            f"（{delta:+.2f}，噪声 ±{self.noise:.2f}）{label}"
        )


def compare(
    metric: str,
    baseline: float | None,
    candidate: float | None,
    noise: float,
    *,
    higher_is_better: bool = True,
) -> Comparison:
    """判断一个指标的变化是不是真的。

    **默认结论是「无法区分」。** 只有差异超过噪声幅度才敢说变好或变差 ——
    评测的价值在于挡住自我安慰，一个见到正数就说变好的比较器毫无意义。
    """
    if baseline is None or candidate is None:
        return Comparison(metric, baseline, candidate, noise, "unavailable")
    delta = candidate - baseline
    if abs(delta) <= noise:
        return Comparison(metric, baseline, candidate, noise, "indistinguishable")
    improved = delta > 0 if higher_is_better else delta < 0
    return Comparison(
        metric, baseline, candidate, noise, "better" if improved else "worse"
    )


def _mean(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"
