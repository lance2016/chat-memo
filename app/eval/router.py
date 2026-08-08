"""评测的 HTTP 接口。

跑一轮要几分钟，所以是**起任务 + 轮询**，不是一个同步请求 ——
同步会撞代理和浏览器的超时，而且没法显示「跑到第几条了」。
和 SSE 相比轮询这里更合适：评测的进度是一秒一条以下的低频事件，
为它开一条长连接不划算，而且刷新页面还能接着看。

只读接口不做任何模型调用，可以随便刷。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.eval import report, service
from app.eval.dataset import (
    CASE_SUFFIX,
    Correction,
    Expectation,
    dump_case,
    load_case,
)
from app.eval.judge import Judge
from app.eval.service import EvalBusy, RunState
from app.llm.factory import get_provider
from app.security import require_api_key
from app.settings_store import resolve_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/eval", tags=["eval"], dependencies=[Depends(require_api_key)])


class CaseOut(BaseModel):
    id: str
    date: str
    note: str
    conversations: int
    memory_files: int
    facts: int
    corrections: int
    forbidden: int
    no_op: bool
    # 标注自查的结果。非空时这条样本不能参与评测
    problems: list[str]


class DatasetOut(BaseModel):
    directory: str
    total: int
    # 有多少条是「这天不该写任何记忆」的反例。只测正例的数据集会奖励一个乱写的模型
    no_op_cases: int
    valid: bool
    cases: list[CaseOut]


class CaseScoreOut(BaseModel):
    case_id: str
    recall: float | None
    correction_rate: float | None
    error_count: int
    no_op_respected: bool | None
    index_issues: int
    memory_writes: int
    tool_calls: int
    seconds: float
    crashed: bool
    judge_failed: bool
    detail: str
    usable: bool


class SummaryOut(BaseModel):
    total: int
    usable: int
    crashed: int
    judge_failed: int
    recall: float | None
    correction_rate: float | None
    errors_total: int
    no_op_respected: float | None
    index_issues_total: int
    index_clean_rate: float | None
    writes_total: int
    tool_calls_total: int
    seconds_total: float


class RunStateOut(BaseModel):
    run_id: str
    status: str
    total: int
    completed: int
    current_case: str
    started_at: str
    finished_at: str
    detail: str
    saved_path: str
    meta: dict[str, str]
    summary: SummaryOut | None
    scores: list[CaseScoreOut]
    mode: str = "run"
    noises: list[NoiseOut] = Field(default_factory=list)
    noise_spread: float = 0.0


class StartRequest(BaseModel):
    cases: str = Field(default=str(service.DEFAULT_CASES))
    only: str = ""
    model: str = ""
    judge_model: str = ""
    judge_provider: str = ""
    # 只跑第 0/1 层。想先确认链路通不通、又不想花裁判的钱时用
    judge: bool = True


class NoiseOut(BaseModel):
    metric: str
    values: list[float]
    spread: float


class NoiseRequest(BaseModel):
    cases: str = Field(default=str(service.DEFAULT_CASES))
    # 测哪条样本。留空取数据集里第一条
    case_id: str = ""
    repeat: int = Field(default=3, ge=2, le=10)
    model: str = ""
    judge_model: str = ""
    judge_provider: str = ""


class ExpectCorrection(BaseModel):
    stale: str
    becomes: str = ""


class ExpectPayload(BaseModel):
    facts: list[str] = Field(default_factory=list)
    corrections: list[ExpectCorrection] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    no_op: bool = False


class CaseDetailOut(BaseModel):
    """一条样本的全部内容，给标注界面用。"""

    id: str
    date: str
    note: str
    memory_before: dict[str, str]
    conversations: list[dict[str, Any]]
    expect: ExpectPayload
    problems: list[str]


class HistoryEntryOut(BaseModel):
    name: str
    created_at: str
    model: str
    judged: bool
    summary: SummaryOut | None


@router.get("/dataset", response_model=DatasetOut)
async def get_dataset(directory: str = str(service.DEFAULT_CASES)) -> Any:
    """数据集现状。界面靠它显示「标了多少、还差什么」。

    这里**不用** `load_dataset()`：那个函数遇到标注问题会整体拒绝，
    而界面恰恰要把有问题的样本显示出来，人才知道该去改哪一条。
    """
    from app.eval.dataset import load_cases

    try:
        cases = load_cases(Path(directory))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    out = [
        CaseOut(
            id=case.id,
            date=case.date,
            note=case.note,
            conversations=len(case.conversations),
            memory_files=len(case.memory_before),
            facts=len(case.expect.facts),
            corrections=len(case.expect.corrections),
            forbidden=len(case.expect.forbidden),
            no_op=case.expect.no_op,
            problems=case.validate(),
        )
        for case in cases
    ]
    return DatasetOut(
        directory=directory,
        total=len(out),
        no_op_cases=sum(1 for c in out if c.no_op),
        valid=all(not c.problems for c in out),
        cases=out,
    )


@router.post("/run", response_model=RunStateOut, status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    payload: StartRequest, session: AsyncSession = Depends(get_session)
) -> Any:
    """起一轮评测。立刻返回，进度去 `GET /api/eval/status` 看。

    202 而不是 200：这时候什么都还没跑完，返回的是一张受理单。
    """
    try:
        cases = service.load_dataset(payload.cases, payload.only)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not cases:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有可跑的样本")

    # 必须是生效配置（数据库覆盖叠加在 .env 上），否则评的不是你实际在跑的那套。
    settings = await resolve_settings(session)
    provider, built_judge = _providers(
        settings, payload.model, payload.judge_provider, payload.judge_model
    )
    judge = built_judge if payload.judge else None

    try:
        state = service.registry.start(
            cases,
            provider,
            judge,
            settings=settings,
            meta={
                "cases": payload.cases,
                "model": getattr(provider, "model_name", ""),
                "judge_model": payload.judge_model,
                "judged": str(judge is not None),
            },
        )
    except EvalBusy as exc:
        # 409 而不是 400：请求本身没问题，只是现在这个时候不行。
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _state_out(state)


@router.post("/noise", response_model=RunStateOut, status_code=status.HTTP_202_ACCEPTED)
async def start_noise(
    payload: NoiseRequest, session: AsyncSession = Depends(get_session)
) -> Any:
    """同一条样本连跑 N 次，量出「多大的差异才值得解读」。

    **这是第一次跑评测前该做的第一件事。** 没有它，之后所有的「改了 prompt
    提升了 3 个点」都可能只是同一份输入的正常抖动。
    """
    try:
        cases = service.load_dataset(payload.cases, payload.case_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not cases:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有可用的样本")

    settings = await resolve_settings(session)
    provider, judge = _providers(settings, payload.model, payload.judge_provider, payload.judge_model)
    try:
        state = service.registry.start_noise(
            cases[0],
            provider,
            judge,
            repeat=payload.repeat,
            settings=settings,
            meta={"case": cases[0].id, "model": getattr(provider, "model_name", "")},
        )
    except EvalBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _state_out(state)


@router.get("/cases/{case_id}", response_model=CaseDetailOut)
async def get_case(case_id: str, directory: str = str(service.DEFAULT_CASES)) -> Any:
    """一条样本的全部内容。标注界面靠它显示对话原文和整理前的记忆。"""
    case = _read_case(directory, case_id)
    return CaseDetailOut(
        id=case.id,
        date=case.date,
        note=case.note,
        memory_before=case.memory_before,
        conversations=[
            {"title": c.title, "messages": [asdict(m) for m in c.messages]}
            for c in case.conversations
        ],
        expect=ExpectPayload(
            facts=case.expect.facts,
            corrections=[
                ExpectCorrection(stale=c.stale, becomes=c.becomes)
                for c in case.expect.corrections
            ],
            forbidden=case.expect.forbidden,
            no_op=case.expect.no_op,
        ),
        problems=case.validate(),
    )


@router.put("/cases/{case_id}/expect", response_model=CaseDetailOut)
async def save_expect(
    case_id: str, payload: ExpectPayload, directory: str = str(service.DEFAULT_CASES)
) -> Any:
    """保存标注。

    **只改 `expect`**，对话和记忆快照原样留着 —— 那两样是冻结的输入，
    动了就不再是同一条样本，之前跑出来的结果也就没法比了。

    保存时不拒绝有问题的标注：标到一半存盘很常见。问题回给界面显示，
    真正拦住的地方在开跑前（`load_dataset`）。
    """
    from dataclasses import replace as dataclass_replace

    case = _read_case(directory, case_id)
    updated = dataclass_replace(
        case,
        expect=Expectation(
            facts=[item.strip() for item in payload.facts if item.strip()],
            corrections=[
                Correction(stale=c.stale.strip(), becomes=c.becomes.strip())
                for c in payload.corrections
                if c.stale.strip()
            ],
            forbidden=[item.strip() for item in payload.forbidden if item.strip()],
            no_op=payload.no_op,
        ),
    )
    dump_case(updated, _case_path(directory, case_id))
    return await get_case(case_id, directory)


@router.get("/status", response_model=RunStateOut | None)
async def get_status() -> Any:
    """当前（或最近一轮）的状态。没跑过返回 null。"""
    state = service.registry.state
    return None if state is None else _state_out(state)


@router.post("/acknowledge", response_model=RunStateOut | None)
async def acknowledge() -> Any:
    """确认看过「上一轮被打断」的提示，清掉记号。"""
    service.registry.acknowledge()
    state = service.registry.state
    return None if state is None else _state_out(state)


@router.post("/cancel", response_model=RunStateOut | None)
async def cancel_run() -> Any:
    """停掉正在跑的那轮。已经花掉的 token 收不回来，但可以不再往下烧。"""
    service.registry.cancel()
    state = service.registry.state
    return None if state is None else _state_out(state)


@router.get("/runs", response_model=list[HistoryEntryOut])
async def list_runs(limit: int = 20) -> Any:
    """历史结果，新的在前。**这才是真正的历史** —— 内存里只留最近一轮。"""
    directory = report.DEFAULT_DIR
    if not directory.exists():
        return []

    entries: list[HistoryEntryOut] = []
    # 跳过 .running.json —— 那是进行中的记号，不是一轮结果。
    results = [p for p in directory.glob("*.json") if not p.name.startswith(".")]
    for path in sorted(results, reverse=True)[:limit]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 半截文件不该让整个历史列表挂掉 —— 跳过并留个日志。
            logger.warning("评测结果文件读不了，已跳过: %s", path.name)
            continue
        meta = raw.get("meta") or {}
        entries.append(
            HistoryEntryOut(
                name=path.stem,
                created_at=str(meta.get("created_at") or ""),
                model=str(meta.get("model") or ""),
                judged=str(meta.get("judged", "")).lower() in ("true", "1"),
                summary=_summary_out(raw.get("summary")),
            )
        )
    return entries


@router.get("/runs/{name}")
async def get_run(name: str) -> Any:
    """某一轮的完整结果，含逐条判定和裁判给的证据。

    `name` 只允许文件名本身 —— 它来自 URL，拼进路径前必须挡住穿越。
    """
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法的结果名")
    path = report.DEFAULT_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有这一轮结果")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "结果文件损坏") from exc


@router.post("/export")
async def export_case(
    day: dt.date, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """把某一天导成待标注样本，写进数据集目录。

    `expect` 留空 —— 期望必须人来标。让模型标期望再拿去评模型，
    等于让它自己出考卷。
    """
    from app.eval.dataset import CASE_SUFFIX, dump_case
    from app.eval.export import export_day

    try:
        case = await export_day(session, day)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    path = service.DEFAULT_CASES / f"{case.id}{CASE_SUFFIX}"
    dump_case(case, path)
    return {
        "path": str(path),
        "id": case.id,
        "conversations": len(case.conversations),
        "memory_files": len(case.memory_before),
        # 版本记录不全时快照会偏空，这样的样本不补齐 memory_before 就不能用
        "snapshot_empty": not case.memory_before,
    }


def _providers(settings, model: str, judge_provider: str, judge_model: str):
    """按同一套规则造被评的 provider 和裁判 provider。

    抽出来是因为 run 和 noise 两条路必须用**完全一样**的装配 ——
    噪声是拿来解释 run 的分数波动的，两边模型不一致的话这个数就没有意义了。
    """
    provider = get_provider(
        settings, model_override=model or settings.consolidate_model
    )
    judge_settings = (
        settings.model_copy(update={"provider": judge_provider})
        if judge_provider
        else settings
    )
    return provider, Judge(get_provider(judge_settings, model_override=judge_model))


def _case_path(directory: str, case_id: str) -> Path:
    """样本文件路径。`case_id` 来自 URL，拼进路径前必须挡住穿越。"""
    if "/" in case_id or "\\" in case_id or case_id.startswith("."):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法的样本名")
    return Path(directory) / f"{case_id}{CASE_SUFFIX}"


def _read_case(directory: str, case_id: str):
    path = _case_path(directory, case_id)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有这条样本")
    try:
        return load_case(path)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"样本读不了：{exc}"
        ) from exc


def _state_out(state: RunState) -> RunStateOut:
    return RunStateOut(
        run_id=state.run_id,
        status=state.status,
        total=state.total,
        completed=state.completed,
        current_case=state.current_case,
        started_at=state.started_at,
        finished_at=state.finished_at,
        detail=state.detail,
        saved_path=state.saved_path,
        meta=state.meta,
        mode=state.mode,
        noises=[NoiseOut(**asdict(noise)) for noise in state.noises],
        noise_spread=state.noise_spread,
        summary=SummaryOut(**asdict(state.summary)) if state.summary else None,
        scores=[
            CaseScoreOut(**asdict(score), usable=score.usable) for score in state.scores
        ],
    )


def _summary_out(raw: object) -> SummaryOut | None:
    if not isinstance(raw, dict):
        return None
    try:
        return SummaryOut(**raw)
    except (TypeError, ValueError):
        return None
