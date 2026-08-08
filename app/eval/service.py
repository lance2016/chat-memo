"""评测的编排层：CLI 和 HTTP 接口共用的那一段。

**为什么单独抽一层**：同一件事有两个入口时，最容易出的问题不是写两遍，
而是两遍慢慢长歪 —— 界面上跑出来的分数和命令行跑出来的对不上，然后没人知道
该信哪个。所以「跑一轮评测」只有这一个实现，CLI 和 router 都只是它的外壳，
各自只负责把结果变成终端文本或 JSON。

运行状态放进程内存，不落库。理由和 `debug/recorder.py` 一样：单进程单人用，
而且真正要留存的东西（每轮的完整结果）已经写进 `eval-runs/*.json` 了，
内存里这份只是「现在跑到第几条」，重启即弃是对的。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.eval import report
from app.eval.dataset import EvalCase, load_cases
from app.eval.judge import Judge, JudgeVerdict
from app.eval.metrics import CaseScore, Summary, score_case, summarize
from app.eval.runner import CaseRun, run_case
from app.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

DEFAULT_CASES = Path("evals/cases")

# 跑到一半留在磁盘上的记号。进程没了它还在，用来说清楚「上一轮被打断了」。
RUNNING_MARKER = ".running.json"

# 开始跑某条样本：(已完成数, 总数, 样本 id)
StartHook = Callable[[int, int, str], None]
# 某条样本跑完：(已完成数, 总数, 样本 id, 这次的运行结果)
DoneHook = Callable[[int, int, str, "CaseRun"], None]


class EvalBusy(RuntimeError):
    """已经有一轮在跑了。

    并发跑两轮没有任何意义 —— 烧双倍 token、抢同一个 provider 的限流额度，
    而且两轮的结果会交替写进 eval-runs，baseline 从此对不上。
    """


@dataclass
class ExecutionResult:
    runs: dict[str, CaseRun] = field(default_factory=dict)
    verdicts: dict[str, JudgeVerdict] = field(default_factory=dict)
    scores: list[CaseScore] = field(default_factory=list)
    summary: Summary = field(default_factory=Summary)


async def execute(
    cases: list[EvalCase],
    provider: LLMProvider,
    judge: Judge | None,
    *,
    settings: Settings | None = None,
    on_start: StartHook | None = None,
    on_done: DoneHook | None = None,
) -> ExecutionResult:
    """顺序跑完一批样本。

    不并发。并发能快好几倍，但会把「每条样本各自的耗时」搅成一团，而耗时是第 1 层
    指标之一；更要紧的是几条样本同时打同一个 provider 容易撞限流，而失败会被记成
    质量问题。二十来条样本，慢几分钟换干净的数字是划算的。

    两个钩子分别对应「开始跑第 N 条」和「第 N 条跑完了」。分开是因为一条样本要跑
    一分钟上下，界面上只显示「已完成 3 条」而不说正在跑哪条，那一分钟里看着就像卡死了。
    """
    result = ExecutionResult()

    for index, case in enumerate(cases):
        if on_start is not None:
            on_start(index, len(cases), case.id)

        run = await run_case(case, provider, settings=settings)
        result.runs[case.id] = run

        verdict = None
        if judge is not None and not run.crashed:
            verdict = await judge.judge(case, run.memory_after, run.transcript)
            result.verdicts[case.id] = verdict

        result.scores.append(score_case(case, run, verdict))
        if on_done is not None:
            on_done(index + 1, len(cases), case.id, run)

    result.summary = summarize(result.scores)
    return result


def load_dataset(directory: Path | str = DEFAULT_CASES, only: str = "") -> list[EvalCase]:
    """读数据集，标注有问题就拒绝跑。

    不是跳过坏样本继续跑 —— 一条标错的样本会安静地拉低分数好几轮，
    而人只会去怀疑模型。宁可当场报错。
    """
    cases = load_cases(Path(directory))
    if only:
        cases = [c for c in cases if only in c.id]
    problems = [(c.id, c.validate()) for c in cases]
    invalid = [(case_id, items) for case_id, items in problems if items]
    if invalid:
        detail = "；".join(f"{case_id}: {'、'.join(items)}" for case_id, items in invalid)
        raise ValueError(f"数据集有标注问题，先修再跑 —— {detail}")
    return cases


# ---------- 运行状态 ----------


@dataclass
class RunState:
    """一轮评测的实时状态。接口轮询它，CLI 不用。"""

    run_id: str
    status: str  # running | done | failed
    total: int = 0
    completed: int = 0
    current_case: str = ""
    started_at: str = ""
    finished_at: str = ""
    detail: str = ""
    # 跑完才有
    summary: Summary | None = None
    scores: list[CaseScore] = field(default_factory=list)
    saved_path: str = ""
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.status == "running"


class EvalRegistry:
    """当前这一轮 + 最近一轮的结果。

    只保留一轮历史：**真正的历史在 `eval-runs/*.json` 里**，那才是能对比的东西。
    内存里这份的唯一职责是让界面在跑的过程中有东西可显示。

    **但内存状态活不过进程重启**，而一轮评测要跑好几分钟：开发时改一行后端代码触发
    热重载，跑到一半的评测就没了，界面上状态回到「从没跑过」—— 烧掉的 token 和几分钟
    等待一起消失，还看不出发生过什么。所以开跑时在磁盘上留一个记号，进程没了它还在，
    下次问状态时就能说清楚「上一轮被打断了」而不是假装无事发生。
    """

    def __init__(self, marker_dir: Path | None = None) -> None:
        self._state: RunState | None = None
        self._task: asyncio.Task[None] | None = None
        self._marker_dir = marker_dir

    @property
    def marker_path(self) -> Path:
        # 延迟取值：测试会 monkeypatch report.DEFAULT_DIR，构造时取就固定住了。
        return (self._marker_dir or report.DEFAULT_DIR) / RUNNING_MARKER

    @property
    def state(self) -> RunState | None:
        if self._state is not None:
            return self._state
        return self._interrupted_state()

    def _interrupted_state(self) -> RunState | None:
        """内存里没有、磁盘上却留着记号 = 上一轮被进程重启打断了。"""
        marker = self.marker_path
        if not marker.exists():
            return None
        try:
            raw = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            marker.unlink(missing_ok=True)
            return None
        return RunState(
            run_id=str(raw.get("run_id") or ""),
            status="interrupted",
            total=int(raw.get("total") or 0),
            completed=int(raw.get("completed") or 0),
            started_at=str(raw.get("started_at") or ""),
            detail="这轮评测没跑完就中断了（多半是后端进程重启）。结果没有保存，需要重跑。",
            meta={str(k): str(v) for k, v in (raw.get("meta") or {}).items()},
        )

    def _write_marker(self, state: RunState) -> None:
        try:
            self.marker_path.parent.mkdir(parents=True, exist_ok=True)
            self.marker_path.write_text(
                json.dumps(
                    {
                        "run_id": state.run_id,
                        "total": state.total,
                        "completed": state.completed,
                        "started_at": state.started_at,
                        "meta": state.meta,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            # 记号只是为了事后说清楚，写不了不该让评测本身跑不起来。
            logger.warning("评测进行中的记号写不了，中断后将无法追溯", exc_info=True)

    def _clear_marker(self) -> None:
        self.marker_path.unlink(missing_ok=True)

    def acknowledge(self) -> None:
        """确认看过中断提示，清掉记号。"""
        self._clear_marker()

    def start(
        self,
        cases: list[EvalCase],
        provider: LLMProvider,
        judge: Judge | None,
        *,
        meta: dict[str, str],
        settings: Settings | None = None,
        directory: Path = report.DEFAULT_DIR,
    ) -> RunState:
        """起一轮后台评测，立刻返回状态。跑着的时候再调会抛 `EvalBusy`。"""
        if self._state is not None and self._state.running:
            raise EvalBusy("已经有一轮评测在跑了")

        state = RunState(
            run_id=uuid.uuid4().hex[:8],
            status="running",
            total=len(cases),
            started_at=_now(),
            meta=meta,
        )
        self._state = state
        self._write_marker(state)
        self._task = asyncio.create_task(
            self._run(state, cases, provider, judge, directory, settings)
        )
        return state

    async def _run(
        self,
        state: RunState,
        cases: list[EvalCase],
        provider: LLMProvider,
        judge: Judge | None,
        directory: Path,
        settings: Settings | None = None,
    ) -> None:
        def on_start(completed: int, total: int, case_id: str) -> None:
            state.completed = completed
            state.total = total
            state.current_case = case_id

        def on_done(completed: int, total: int, case_id: str, run: CaseRun) -> None:
            state.completed = completed
            state.current_case = ""

        try:
            result = await execute(
                cases,
                provider,
                judge,
                settings=settings,
                on_start=on_start,
                on_done=on_done,
            )
        except Exception as exc:
            # 不 re-raise：这是后台任务，抛出去只会变成一条 "Task exception was never
            # retrieved"，界面上什么也看不到。状态里记下来才有人看得见。
            logger.exception("评测执行失败: run_id=%s", state.run_id)
            state.status = "failed"
            state.detail = f"{type(exc).__name__}: {exc}"
            state.finished_at = _now()
            self._clear_marker()
            return

        state.summary = result.summary
        state.scores = result.scores
        state.saved_path = str(
            report.save_run(
                result.summary,
                result.scores,
                result.runs,
                result.verdicts,
                meta=dict(state.meta),
                directory=directory,
            )
        )
        state.status = "done"
        state.finished_at = _now()
        self._clear_marker()
        logger.info(
            "评测完成 run_id=%s：%d/%d 可用 · 结果存到 %s",
            state.run_id, result.summary.usable, result.summary.total, state.saved_path,
        )

    async def wait(self) -> None:
        """等当前这轮跑完。给测试和进程关闭用，界面走轮询。"""
        if self._task is not None:
            await asyncio.shield(asyncio.gather(self._task, return_exceptions=True))

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            if self._state is not None:
                self._state.status = "failed"
                self._state.detail = "已取消"
                self._state.finished_at = _now()
        self._clear_marker()


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# 单进程单人用，一个模块级实例就够 —— 和 tts.tickets、debug.recorder 同一个路子。
registry = EvalRegistry()
