"""评测的编排层与接口。

这一层的存在理由是**只有一份实现**：CLI 和界面调的是同一个 `service.execute`。
所以这些用例钉的不是「能不能跑」，而是那些一旦松掉就会让两个入口悄悄长歪、
或者让界面显示出错误状态的地方。
"""

import asyncio
import datetime as dt
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.session import get_session
from app.eval import service
from app.eval.dataset import EvalCase, EvalConversation, EvalMessage, Expectation, dump_case
from app.eval.service import EvalBusy, EvalRegistry
from app.llm.anthropic_provider import AnthropicProvider
from app.main import create_app
from tests.fakes import FakeAnthropic, text_turn, tool_turn

TODAY = dt.date.today()


def provider_with(turns: list) -> AnthropicProvider:
    return AnthropicProvider(
        settings=Settings(anthropic_api_key="test"), client=FakeAnthropic(turns)
    )


def make_case(case_id: str = "case-1", **overrides) -> EvalCase:
    defaults = dict(
        id=case_id,
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


def writing_provider() -> AnthropicProvider:
    return provider_with(
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


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    """每个用例一个干净的 registry —— 模块级单例会把上一个用例的状态漏过来。"""
    monkeypatch.setattr(service, "registry", EvalRegistry())


# ---------- 编排 ----------


async def test_hooks_report_both_start_and_finish() -> None:
    """一条样本要跑一分钟上下。

    只报「已完成 N 条」而不说正在跑哪条，那一分钟里界面看着就像卡死了。
    """
    events: list[tuple[str, int, str]] = []

    await service.execute(
        [make_case("a"), make_case("b")],
        provider_with([text_turn("摘要"), text_turn("无需改动")] * 2),
        None,
        on_start=lambda done, total, case_id: events.append(("start", done, case_id)),
        on_done=lambda done, total, case_id, run: events.append(("done", done, case_id)),
    )

    assert events == [
        ("start", 0, "a"), ("done", 1, "a"),
        ("start", 1, "b"), ("done", 2, "b"),
    ]


async def test_execute_returns_a_summary_over_all_cases() -> None:
    result = await service.execute(
        [make_case("a")], writing_provider(), None
    )

    assert result.summary.total == 1
    assert result.runs["a"].diff.created == ("/memories/profile/preferences.md",)


# ---------- 并发与失败 ----------


async def test_a_second_run_is_rejected_while_one_is_going(tmp_path) -> None:
    """并发跑两轮烧双倍 token、抢同一个限流额度，两轮结果还会交替写进 eval-runs。

    `directory=tmp_path` 不能省 —— 默认值是真实的 `eval-runs/`，测试会往仓库里
    写结果文件，然后污染你下一次真实运行的 baseline。
    """
    registry = EvalRegistry(marker_dir=tmp_path)
    registry.start([make_case()], writing_provider(), None, meta={}, directory=tmp_path)

    with pytest.raises(EvalBusy):
        registry.start([make_case()], writing_provider(), None, meta={}, directory=tmp_path)

    await registry.wait()


async def test_a_failed_run_shows_up_as_state_not_as_a_lost_exception(tmp_path) -> None:
    """后台任务抛出去只会变成一条没人看的 warning，界面上什么也看不到。"""
    registry = EvalRegistry(marker_dir=tmp_path)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("provider 挂了")

    original = service.execute
    service.execute = boom
    try:
        registry.start([make_case()], writing_provider(), None, meta={}, directory=tmp_path)
        await registry.wait()
    finally:
        service.execute = original

    assert registry.state is not None
    assert registry.state.status == "failed"
    assert "provider 挂了" in registry.state.detail


async def test_a_finished_run_is_saved_and_marked_done(tmp_path) -> None:
    registry = EvalRegistry(marker_dir=tmp_path)
    registry.start([make_case()], writing_provider(), None, meta={"model": "fake"}, directory=tmp_path)
    await registry.wait()

    state = registry.state
    assert state is not None and state.status == "done"
    assert state.completed == state.total == 1
    assert Path(state.saved_path).exists()
    assert json.loads(Path(state.saved_path).read_text())["meta"]["model"] == "fake"


# ---------- 数据集校验 ----------


def test_load_dataset_refuses_a_badly_annotated_case(tmp_path) -> None:
    """一条标错的样本会安静地拉低分数好几轮，而人只会去怀疑模型。"""
    bad = make_case("bad", expect=Expectation(no_op=True, facts=["不该同时标"]))
    dump_case(bad, tmp_path / "bad.json")

    with pytest.raises(ValueError, match="标注问题"):
        service.load_dataset(tmp_path)


# ---------- 接口 ----------


async def test_dataset_endpoint_surfaces_problems_instead_of_refusing(
    client: AsyncClient, tmp_path
) -> None:
    """界面要把有问题的样本显示出来，人才知道该改哪一条 —— 不能整体 400。"""
    dump_case(make_case("good"), tmp_path / "good.json")
    dump_case(
        make_case("bad", expect=Expectation(no_op=True, facts=["矛盾"])),
        tmp_path / "bad.json",
    )

    body = (await client.get(f"/api/eval/dataset?directory={tmp_path}")).json()

    assert body["total"] == 2 and body["valid"] is False
    problems = {case["id"]: case["problems"] for case in body["cases"]}
    assert problems["good"] == [] and problems["bad"]


async def test_status_is_null_before_anything_has_run(client: AsyncClient) -> None:
    assert (await client.get("/api/eval/status")).json() is None


async def test_starting_a_run_while_busy_returns_409(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    """409 而不是 400：请求本身没问题，只是现在这个时候不行。"""
    dump_case(make_case("only"), tmp_path / "only.json")
    monkeypatch.setattr(
        service.registry, "start", lambda *a, **k: (_ for _ in ()).throw(EvalBusy("忙"))
    )

    response = await client.post(
        "/api/eval/run", json={"cases": str(tmp_path), "judge": False}
    )

    assert response.status_code == 409


async def test_missing_dataset_is_a_400_not_a_500(client: AsyncClient) -> None:
    response = await client.post("/api/eval/run", json={"cases": "/nope/nowhere"})

    assert response.status_code == 400


async def test_result_name_cannot_escape_the_results_directory(
    client: AsyncClient,
) -> None:
    """name 来自 URL，拼进路径前必须挡住穿越。"""
    for evil in ("../../etc/passwd", "..%2f..%2fsecret", ".ssh"):
        response = await client.get(f"/api/eval/runs/{evil}")
        assert response.status_code in (400, 404), evil


async def test_history_skips_a_corrupt_file_instead_of_failing(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    """半截文件不该让整个历史列表挂掉。"""
    from app.eval import report as report_module

    monkeypatch.setattr(report_module, "DEFAULT_DIR", tmp_path)
    (tmp_path / "20260101-000000.json").write_text("{ 半截", encoding="utf-8")
    (tmp_path / "20260102-000000.json").write_text(
        json.dumps({"meta": {"created_at": "2026-01-02", "model": "m"}, "summary": {}}),
        encoding="utf-8",
    )

    body = (await client.get("/api/eval/runs")).json()

    assert [entry["name"] for entry in body] == ["20260102-000000"]


async def test_history_is_newest_first(client: AsyncClient, tmp_path, monkeypatch) -> None:
    from app.eval import report as report_module

    monkeypatch.setattr(report_module, "DEFAULT_DIR", tmp_path)
    for name in ("20260101-000000", "20260103-000000", "20260102-000000"):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"meta": {}, "summary": {}}), encoding="utf-8"
        )

    names = [entry["name"] for entry in (await client.get("/api/eval/runs")).json()]

    assert names == ["20260103-000000", "20260102-000000", "20260101-000000"]


# ---------- 被打断的一轮 ----------


async def test_an_interrupted_run_is_reported_not_forgotten(tmp_path, monkeypatch) -> None:
    """进程重启会带走内存状态。

    一轮评测跑好几分钟，热重载一次就没了 —— 如果状态直接回到「从没跑过」，
    烧掉的 token 和几分钟等待一起消失，还看不出发生过什么。
    """
    from app.eval import report as report_module

    monkeypatch.setattr(report_module, "DEFAULT_DIR", tmp_path)
    dead = EvalRegistry(marker_dir=tmp_path)
    dead.start([make_case()], writing_provider(), None, meta={}, directory=tmp_path)

    # 模拟进程重启：新 registry，磁盘上的记号还在
    revived = EvalRegistry(marker_dir=tmp_path)
    state = revived.state

    assert state is not None and state.status == "interrupted"
    assert "中断" in state.detail
    await dead.wait()


async def test_a_finished_run_leaves_no_interrupted_marker(tmp_path, monkeypatch) -> None:
    """跑完要把记号清掉，否则下次启动会谎报「上一轮被打断」。"""
    from app.eval import report as report_module

    monkeypatch.setattr(report_module, "DEFAULT_DIR", tmp_path)
    registry = EvalRegistry(marker_dir=tmp_path)
    registry.start([make_case()], writing_provider(), None, meta={}, directory=tmp_path)
    await registry.wait()

    assert EvalRegistry(marker_dir=tmp_path).state is None


async def test_acknowledging_clears_the_interrupted_notice(tmp_path, monkeypatch) -> None:
    from app.eval import report as report_module

    monkeypatch.setattr(report_module, "DEFAULT_DIR", tmp_path)
    dead = EvalRegistry(marker_dir=tmp_path)
    dead.start([make_case()], writing_provider(), None, meta={}, directory=tmp_path)
    await dead.wait()
    (tmp_path / service.RUNNING_MARKER).write_text('{"run_id": "x", "total": 1}', encoding="utf-8")

    revived = EvalRegistry(marker_dir=tmp_path)
    assert revived.state is not None
    revived.acknowledge()

    assert revived.state is None


async def test_the_running_marker_is_not_listed_as_a_result(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    """记号是隐藏文件，不是一轮结果 —— 混进历史列表会显示成一条空记录。"""
    from app.eval import report as report_module

    monkeypatch.setattr(report_module, "DEFAULT_DIR", tmp_path)
    (tmp_path / service.RUNNING_MARKER).write_text('{"run_id": "x"}', encoding="utf-8")
    (tmp_path / "20260102-000000.json").write_text(
        json.dumps({"meta": {}, "summary": {}}), encoding="utf-8"
    )

    names = [entry["name"] for entry in (await client.get("/api/eval/runs")).json()]

    assert names == ["20260102-000000"]
