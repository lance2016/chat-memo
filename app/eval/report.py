"""报告：终端看结论，JSON 留证据。

两个出口是有分工的。终端那份要能一眼看完，所以只有汇总和异常样本 —— 一屏放不下的
报告没人会读完。JSON 那份存全量，包括每条样本的逐事实判定和裁判给的证据；
半年后想知道「当时为什么判它没召回」，只有那份文件答得上来。

存 JSON 还有一个更实际的理由：**这次的 JSON 就是下次的 baseline**。
没有留存就只能和记忆里的印象比，而印象永远觉得改动是有效的。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.eval.judge import JudgeVerdict
from app.eval.metrics import CaseScore, Comparison, Noise, Summary
from app.eval.runner import CaseRun

DEFAULT_DIR = Path("eval-runs")


def render_summary(summary: Summary, title: str = "评测结果") -> str:
    lines = [f"\n{title}", "─" * 46]
    lines += [f"  {name:<12} {value}" for name, value in summary.render()]
    return "\n".join(lines)


def render_cases(scores: list[CaseScore]) -> str:
    """逐样本一行。宽度是按 80 列终端定的 —— 换行的表格不如没有表格。"""
    header = f"  {'样本':<26}{'召回':>6}{'修正':>6}{'错误':>6}{'索引':>6}{'写入':>6}"
    lines = [header, "  " + "─" * 56]
    for score in scores:
        if score.crashed:
            lines.append(f"  {_clip(score.case_id):<26}{'崩溃':>6}  {score.detail[:24]}")
            continue
        judged = "裁判失败" if score.judge_failed else ""
        lines.append(
            f"  {_clip(score.case_id):<26}"
            f"{_pct(score.recall):>6}"
            f"{_pct(score.correction_rate):>6}"
            f"{score.error_count:>6}"
            f"{score.index_issues:>6}"
            f"{score.memory_writes:>6}"
            f"  {judged}"
        )
    return "\n".join(lines)


def render_attention(scores: list[CaseScore], runs: dict[str, CaseRun]) -> str:
    """只列需要人去看的样本。

    「哪几条出了问题」比「平均分是多少」更能推动改进 —— 平均分只告诉你有问题，
    这一段告诉你去看哪个文件。
    """
    flagged: list[str] = []
    for score in scores:
        reasons = []
        if score.crashed:
            reasons.append(f"执行崩溃（{score.detail}）")
        if score.judge_failed:
            reasons.append("裁判失败，本条指标作废")
        if score.recall is not None and score.recall < 1.0:
            reasons.append(f"召回 {score.recall:.0%}")
        if score.error_count:
            reasons.append(f"引入 {score.error_count} 条错误")
        if score.no_op_respected is False:
            reasons.append("该沉默的一天写了记忆")
        if score.index_issues:
            run = runs.get(score.case_id)
            detail = run.audit.summary() if run else f"{score.index_issues} 个索引问题"
            reasons.append(detail)
        if reasons:
            flagged.append(f"  · {score.case_id}：" + "；".join(reasons))

    if not flagged:
        return "\n  全部样本没有需要关注的问题。"
    return "\n需要关注\n" + "\n".join(flagged)


def render_noise(noises: list[Noise]) -> str:
    lines = ["\n噪声（同一份输入重复跑）", "─" * 46]
    lines += [f"  {noise.render()}" for noise in noises]
    lines.append("  比这个幅度小的差异不要解读成改进。")
    return "\n".join(lines)


def render_comparison(comparisons: list[Comparison]) -> str:
    lines = ["\n与 baseline 对比", "─" * 46]
    lines += [f"  {c.render()}" for c in comparisons]
    return "\n".join(lines)


def save_run(
    summary: Summary,
    scores: list[CaseScore],
    runs: dict[str, CaseRun],
    verdicts: dict[str, JudgeVerdict],
    *,
    meta: dict[str, Any],
    directory: Path = DEFAULT_DIR,
) -> Path:
    """存一轮完整结果，返回文件路径。文件名带时间戳，天然按时间排序。"""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    payload = {
        "meta": {**meta, "created_at": dt.datetime.now().isoformat(timespec="seconds")},
        "summary": asdict(summary),
        "cases": [
            {
                **asdict(score),
                # 判定的证据链：改了 prompt 之后回头看「它当时到底写了什么」
                "diff": asdict(runs[score.case_id].diff)
                if score.case_id in runs
                else {},
                "memory_after": runs[score.case_id].memory_after
                if score.case_id in runs
                else {},
                "verdict": asdict(verdicts[score.case_id])
                if score.case_id in verdicts
                else {},
            }
            for score in scores
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def load_summary(path: Path) -> Summary:
    """读一份历史结果当 baseline。只取 summary —— 对比是汇总级的。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Summary(**raw["summary"])


def latest_run(directory: Path = DEFAULT_DIR) -> Path | None:
    if not directory.exists():
        return None
    runs = sorted(directory.glob("*.json"))
    return runs[-1] if runs else None


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _clip(text: str, width: int = 24) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"
