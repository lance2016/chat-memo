"""评测的命令行入口：`python -m app.eval <命令>`。

为什么是 CLI 而不是接口或界面：评测要花几分钟、要烧 token、结论要人来读和判断。
把它做成设置页上的一个按钮，只会让人误以为它像刷新一样廉价。它是一个**开发动作**，
和跑测试同级，不该出现在给自己用的产品界面里。

三个命令，对应 docs/evaluation.md 的三个阶段：

    export   把真实的一天导成待标注样本（阶段 2 的入口）
    run      对数据集重放整理并打分（阶段 3/4）
    noise    同一条样本连跑几次，量出解释力下限（第五节，**第一次跑评测前先跑它**）
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import sys
from pathlib import Path

from app.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.eval import report, service
from app.eval.dataset import CASE_SUFFIX, EvalCase, dump_case
from app.eval.export import export_day
from app.eval.judge import Judge, JudgeVerdict
from app.eval.metrics import (
    compare,
    measure_noise,
    score_case,
)
from app.eval.runner import CaseRun, run_case
from app.eval.service import ExecutionResult
from app.llm.factory import get_provider
from app.llm.provider import LLMProvider
from app.logging_setup import setup_logging
from app.settings_store import resolve_settings

logger = logging.getLogger("app.eval")

DEFAULT_CASES = Path("evals/cases")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.log_level, True, False, "pretty")
    return asyncio.run(_dispatch(args))


async def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "export":
        return await _export(args)
    if args.command == "run":
        return await _run(args)
    if args.command == "noise":
        return await _noise(args)
    raise AssertionError(f"未知命令 {args.command}")


# ---------- export ----------


async def _export(args: argparse.Namespace) -> int:
    """从生产库导出一天。**只有这个命令碰真实数据库**，其余命令都在内存库里跑。"""
    day = dt.date.fromisoformat(args.day)
    async with get_sessionmaker()() as session:
        case = await export_day(session, day)

    out = Path(args.out)
    path = out / f"{case.id}{CASE_SUFFIX}" if out.is_dir() or not out.suffix else out
    dump_case(case, path)

    print(f"已导出 {path}")
    print(f"  {len(case.conversations)} 个会话 · 整理前记忆 {len(case.memory_before)} 个文件")
    if not case.memory_before:
        # 静默地给出一个空起点会让这条样本永远评不对，必须当场说清楚。
        print("  ⚠️  整理前记忆为空 —— 这天可能早于 memory_versions 建表，"
              "需要手工补 memory_before，否则别用这条样本")
    print("  下一步：编辑这个文件，把 expect 标出来（facts / corrections / forbidden / no_op）")
    return 0


# ---------- run ----------


async def _run(args: argparse.Namespace) -> int:
    cases = _load(args.cases, args.only)
    if not cases:
        print("没有可跑的样本", file=sys.stderr)
        return 1

    settings = await _effective_settings()
    # 先打印再构造 provider：缺 key 时 SDK 会当场抛异常，而那条异常没提到
    # 「你以为用的是哪把 key」—— 恰恰是这时候最需要看到的一行。
    print(f"数据集 {args.cases} · {len(cases)} 条样本")
    print(f"  {_credential_hint(settings)}")

    provider = _consolidate_provider(settings, args.model)
    judge = Judge(_judge_provider(settings, args)) if not args.no_judge else None
    print(f"整理模型 {getattr(provider, 'model_name', '未知')}"
          + ("" if judge is None else f" · 裁判 {args.judge_model or '同上'}"))

    result = await _execute(cases, provider, judge)
    runs, verdicts, scores = result.runs, result.verdicts, result.scores
    summary = result.summary

    print(report.render_summary(summary))
    print()
    print(report.render_cases(scores))
    print(report.render_attention(scores, runs))

    baseline_path = _baseline_path(args)
    if baseline_path is not None:
        _print_comparison(baseline_path, summary, args.noise)

    saved = report.save_run(
        summary,
        scores,
        runs,
        verdicts,
        meta={
            "cases": str(args.cases),
            "model": getattr(provider, "model_name", ""),
            "judge_model": args.judge_model,
            "judged": judge is not None,
        },
    )
    print(f"\n完整结果已存到 {saved}")
    return 0


async def _execute(
    cases: list[EvalCase], provider: LLMProvider, judge: Judge | None
) -> ExecutionResult:
    """跑一批样本，边跑边在终端打点。

    编排本身在 `service.execute` 里，HTTP 接口调的是同一个函数 —— 这里只负责把
    进度变成终端上的一行。**两个入口共用一份实现**，否则界面上跑出来的分数和
    命令行跑出来的迟早对不上，然后没人知道该信哪个。
    """

    def on_start(completed: int, total: int, case_id: str) -> None:
        print(f"  [{completed + 1}/{total}] {case_id} …", end="", flush=True)

    def on_done(completed: int, total: int, case_id: str, run: CaseRun) -> None:
        print(f" {run.diff.render()} · {run.seconds:.1f}s"
              + (" · 崩溃" if run.crashed else ""))

    return await service.execute(
        cases, provider, judge, on_start=on_start, on_done=on_done
    )


# ---------- noise ----------


async def _noise(args: argparse.Namespace) -> int:
    """同一条样本连跑 N 次，量出「多大的差异才值得解读」。

    这是**第一次跑评测前该做的第一件事**。没有它，后面所有的「改了 prompt 提升了
    3 个点」都可能只是同一份输入的正常抖动。
    """
    cases = _load(args.cases, args.only)
    if not cases:
        print("没有可跑的样本", file=sys.stderr)
        return 1
    case = cases[0]

    settings = await _effective_settings()
    provider = _consolidate_provider(settings, args.model)
    judge = Judge(_judge_provider(settings, args))

    print(f"噪声测量：{case.id} 连跑 {args.repeat} 次")
    print(f"  {_credential_hint(settings)}")
    recalls: list[float | None] = []
    writes: list[float | None] = []
    for i in range(args.repeat):
        run = await run_case(case, provider, sequence=i)
        verdict = (
            await judge.judge(case, run.memory_after, run.transcript)
            if not run.crashed
            else JudgeVerdict(failed=True)
        )
        score = score_case(case, run, verdict)
        recalls.append(score.recall)
        writes.append(float(score.memory_writes))
        print(f"  第 {i + 1} 次：召回 {_fmt(score.recall)} · "
              f"写入 {score.memory_writes} · {run.seconds:.1f}s")

    noises = [
        measure_noise("事实召回", recalls),
        measure_noise("记忆写入次数", writes),
    ]
    print(report.render_noise(noises))
    print(f"\n把 --noise {noises[0].spread:.2f} 用在之后的 run 上，"
          "小于这个幅度的变化不要当成改进。")
    return 0


# ---------- 装配 ----------


async def _effective_settings() -> Settings:
    """生效配置 = 数据库覆盖层叠加在 .env 之上，和聊天、每日整理读的是同一份。

    **不能用 `get_settings()`**：那是启动快照，漏掉设置页改过的东西。在设置页把
    provider 换成 anthropic 之后，用快照的评测会去评一个你根本没在跑的配置 ——
    正是这套评测反复要挡的那种失败：结果看着像那么回事，偏了却没有任何症状。

    数据库连不上时退回快照并**明说**。评测是开发工具，不该因为没起 compose 就跑不了，
    但也不能默默用一份可能过时的配置。
    """
    try:
        async with get_sessionmaker()() as session:
            return await resolve_settings(session)
    except Exception as exc:
        logger.warning(
            "读不到数据库里的运行时配置，退回 .env 快照（设置页改过的项不会生效）: %s", exc
        )
        return get_settings()


def _credential_hint(settings: Settings) -> str:
    """打印当前用的是哪把 key 的尾号。

    宿主机上跑评测有个很难看出来的坑：shell 里 export 过的同名变量**优先级高于
    `.env`**（pydantic-settings 的行为）。容器从 env_file 读 .env，宿主机读到的却是
    shell 里那把旧的，于是「聊天好好的，评测 401」。尾号一比就看出来了。
    """
    key = (
        settings.anthropic_api_key
        if settings.provider == "anthropic"
        else settings.deepseek_api_key
    )
    if not key:
        return f"provider {settings.provider} · ⚠️ 没有配置 API key"
    return f"provider {settings.provider} · key ****{key[-4:]}（和 .env 对不上就是被 shell 变量盖了）"


def _consolidate_provider(settings: Settings, model: str) -> LLMProvider:
    """被评的那个 provider。默认走 `consolidate_model` —— 评的必须是真实配置。"""
    return get_provider(settings, model_override=model or settings.consolidate_model)


def _judge_provider(settings: Settings, args: argparse.Namespace) -> LLMProvider:
    """裁判默认换一家模型。

    同族模型自评有明确的自我偏好（docs/evaluation.md 第五节），而换一家的成本只是
    换个 provider 名。留空时退回同一个模型 —— 有偏差的评测也好过没有评测，
    但报告里会记下用的是谁，事后能看出来那轮数据该打折。
    """
    if args.judge_provider:
        settings = settings.model_copy(update={"provider": args.judge_provider})
    return get_provider(settings, model_override=args.judge_model)


def _load(directory: Path, only: str) -> list[EvalCase]:
    """读数据集。校验在 `service.load_dataset` 里，和接口共用同一套判定。"""
    try:
        return service.load_dataset(directory, only)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _baseline_path(args: argparse.Namespace) -> Path | None:
    if args.no_baseline:
        return None
    if args.baseline:
        return Path(args.baseline)
    return report.latest_run()


def _print_comparison(path: Path, summary, noise: float) -> None:
    try:
        baseline = report.load_summary(path)
    except (OSError, KeyError, TypeError) as exc:
        logger.warning("baseline 读不了，跳过对比: %s (%s)", path, exc)
        return
    comparisons = [
        compare("事实召回", baseline.recall, summary.recall, noise),
        compare("修正正确率", baseline.correction_rate, summary.correction_rate, noise),
        compare(
            "索引干净率", baseline.index_clean_rate, summary.index_clean_rate, noise
        ),
        compare(
            "引入错误",
            float(baseline.errors_total),
            float(summary.errors_total),
            # 错误是计数不是比例，噪声换算成「至少差一条才算数」
            max(noise, 1.0),
            higher_is_better=False,
        ),
    ]
    print(f"\n（baseline: {path.name}）")
    print(report.render_comparison(comparisons))


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.eval", description="记忆整理的评测"
    )
    parser.add_argument("--log-level", default="WARNING", help="默认只报警告，报告本身走 stdout")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="把真实的一天导成待标注样本")
    export.add_argument("--day", required=True, help="YYYY-MM-DD")
    export.add_argument("--out", default=str(DEFAULT_CASES), help="输出目录或文件路径")

    run = sub.add_parser("run", help="跑一轮评测")
    _add_shared(run)
    run.add_argument("--no-judge", action="store_true", help="只跑第 0/1 层，不花裁判的 token")
    run.add_argument("--baseline", default="", help="对比用的历史结果 JSON，默认取最近一次")
    run.add_argument("--no-baseline", action="store_true", help="不做对比")
    run.add_argument(
        "--noise", type=float, default=0.05,
        help="解释力下限，先用 noise 命令量出来。小于它的差异一律判「无法区分」",
    )

    noise = sub.add_parser("noise", help="同一条样本连跑几次，量出噪声")
    _add_shared(noise)
    noise.add_argument("--repeat", type=int, default=3, help="跑几次，默认 3")
    return parser


def _add_shared(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="数据集目录")
    parser.add_argument("--only", default="", help="只跑 id 包含这段文字的样本")
    parser.add_argument("--model", default="", help="被评的整理模型，默认用 consolidate_model")
    parser.add_argument("--judge-provider", default="", help="裁判的 provider，建议和被评的不同")
    parser.add_argument("--judge-model", default="", help="裁判模型")


if __name__ == "__main__":
    raise SystemExit(main())
