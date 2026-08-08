"""评测数据集的形状校验。

这些用例钉住的是**数据集这份资产不会悄悄烂掉**。样本是手工标的，一条几十分钟，
但它们只是 JSON —— 没有类型检查、没有编译期，改错一个字段名不会有任何东西报错，
等到真跑评测时才发现，那次实验的结果已经白跑了。所以在单测里把契约钉死：
每条样本都能被 `load_case` 读出来、都能通过 `validate()`、索引格式符合
`memory/prompt.py` 的约定。

另一半用例覆盖 `dataset.py` 自己。重点不是「函数能跑」，而是 `validate()` 真的
抓得住标注时最常犯的那两种错 —— 它是标注者唯一的自动检查，漏判等于没有。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db.models import Memory
from app.eval.dataset import (
    Correction,
    EvalCase,
    EvalConversation,
    EvalMessage,
    Expectation,
    dump_case,
    load_case,
    load_cases,
)
from app.memory.audit import audit_index
from app.memory.paths import INDEX_PATH

CASES_DIR = Path(__file__).resolve().parents[1] / "evals" / "cases"


def _cases() -> list[EvalCase]:
    return load_cases(CASES_DIR)


def _case_files() -> list[Path]:
    return sorted(CASES_DIR.glob("*.json"))


# --------------------------------------------------------------- 仓库里的样本


def test_every_case_file_loads_and_validates() -> None:
    """每条样本都必须读得出来且自查无问题 —— 标注错误要在这里挡住，不是跑评测时。"""
    files = _case_files()
    assert files, "evals/cases 下没有样本，数据集是评测的全部资产"
    for path in files:
        case = load_case(path)
        assert case.validate() == [], f"{path.name} 标注有问题"


def test_case_ids_are_unique_and_match_filenames() -> None:
    """id 是报告里逐行对照用的键，重复或和文件名对不上就没法追溯到样本。"""
    for path in _case_files():
        case = load_case(path)
        assert case.id == path.stem, f"{path.name} 的 id 和文件名不一致"
    ids = [case.id for case in _cases()]
    assert len(ids) == len(set(ids))


def test_every_case_freezes_a_memory_snapshot_with_an_index() -> None:
    """快照必须含 MEMORY.md：没有索引，模型的起始视野是空的，起点就不确定了。

    `validate()` 查不到这一条（它不知道记忆的领域约定），所以在这里补。
    """
    for case in _cases():
        assert INDEX_PATH in case.memory_before, f"{case.id} 的快照缺索引文件"


def test_snapshot_indexes_follow_the_documented_format() -> None:
    """快照里的索引要符合 memory/prompt.py 的格式，否则样本自己就是脏输入。

    唯一允许的例外是**故意留的缺口**（06-index-gap 那种）：那是被测的内容本身。
    这里用 audit_index 反过来确认「缺口只出现在设计好的地方」，
    格式错误（malformed）和超长描述（overlong）则一条都不该有。
    """
    for case in _cases():
        memories = [
            Memory(path=path, content=content)
            for path, content in case.memory_before.items()
        ]
        audit = audit_index(memories)
        assert audit.malformed == (), f"{case.id} 的索引有格式不对的行"
        assert audit.overlong == (), f"{case.id} 的索引条目描述超长"
        assert audit.orphaned == (), f"{case.id} 的索引指向了快照里没有的文件"


def test_exactly_the_index_gap_case_has_a_missing_entry() -> None:
    """索引缺口是一条样本刻意布置的陷阱，不能因为改别的样本被误引入。"""
    with_gap = {case.id for case in _cases() if audit_missing(case)}
    assert with_gap == {"06-index-gap"}


def audit_missing(case: EvalCase) -> tuple[str, ...]:
    memories = [
        Memory(path=path, content=content) for path, content in case.memory_before.items()
    ]
    return audit_index(memories).missing


def test_dataset_contains_a_negative_example() -> None:
    """至少一条 no_op：只测正例的评测会奖励一个疯狂写记忆的模型。"""
    no_ops = [case.id for case in _cases() if case.expect.no_op]
    assert no_ops, "数据集里没有 no_op 反例"


def test_dataset_covers_corrections_and_forbidden() -> None:
    """冲突修正和「不该记什么」各要有覆盖 —— 它们对应两类最贵的失败：
    新旧并存、和把一次性内容写进长期记忆。
    """
    cases = _cases()
    assert any(case.expect.corrections for case in cases)
    assert any(case.expect.forbidden for case in cases)


def test_every_case_explains_itself() -> None:
    """note 是给半年后的自己看的。空 note 的样本等于没人知道它在测什么。"""
    for case in _cases():
        assert len(case.note.strip()) >= 30, f"{case.id} 的 note 太短，说不清测什么"


def test_facts_are_single_points_not_paragraphs() -> None:
    """标事实点不标文本：一条 fact 是一句话。写成一段就判不了二值。"""
    for case in _cases():
        for fact in case.expect.facts:
            assert "\n" not in fact, f"{case.id} 有跨行的 fact"
            assert len(fact) <= 60, f"{case.id} 的 fact 写成了段落: {fact!r}"


# --------------------------------------------------------------- dataset.py 本身


def _minimal_case() -> EvalCase:
    return EvalCase(
        id="round-trip",
        date="2026-01-01",
        memory_before={INDEX_PATH: "# 记忆索引\n\n- [基本情况](profile/basics.md) — 居住与工作\n"},
        conversations=[
            EvalConversation(
                title="随口一句",
                messages=[
                    EvalMessage(role="user", text="我搬到广州了"),
                    EvalMessage(role="assistant", text="记下了。"),
                ],
            )
        ],
        expect=Expectation(facts=["用户搬到了广州"], forbidden=["搬家当天的天气"]),
        note="round-trip 用的最小样本",
    )


def test_dump_then_load_round_trips(tmp_path: Path) -> None:
    """存成 JSON 再读回来必须完全相等。

    样本要能 diff、能手改、能进 git，所以落盘格式是纯 JSON 而不是 pickle；
    代价是每个字段都要手写反序列化，漏一个字段不会报错、只会静默丢标注。
    这条用例就是那个会报错的东西。
    """
    case = _minimal_case()
    path = tmp_path / "round-trip.json"
    dump_case(case, path)

    assert load_case(path) == case


def test_dump_keeps_chinese_readable(tmp_path: Path) -> None:
    """中文不能被转义成 \\uXXXX —— 样本要靠肉眼 review 和 git diff 才有意义。"""
    path = tmp_path / "case.json"
    dump_case(_minimal_case(), path)
    raw = path.read_text(encoding="utf-8")

    assert "用户搬到了广州" in raw
    assert raw.endswith("\n")


def test_load_cases_is_sorted_by_filename(tmp_path: Path) -> None:
    """顺序固定，报告才能逐行对照；顺序抖动会让两次运行的 diff 没法看。"""
    for name in ("02-b", "01-a", "03-c"):
        case = _minimal_case()
        dump_case(EvalCase(**{**case.__dict__, "id": name}), tmp_path / f"{name}.json")

    assert [case.id for case in load_cases(tmp_path)] == ["01-a", "02-b", "03-c"]


def test_id_must_match_the_filename(tmp_path: Path) -> None:
    """报告按 id 索引，目录按文件名排序。两者对不上时，报告里的样本名指不回文件 ——
    而那正是出了问题要去看它的时候。"""
    case = _minimal_case()
    dump_case(EvalCase(**{**case.__dict__, "id": "别的名字"}), tmp_path / "01-a.json")

    with pytest.raises(ValueError, match="id 与文件名不一致"):
        load_cases(tmp_path)


def test_load_cases_rejects_a_missing_directory(tmp_path: Path) -> None:
    """路径打错时要立刻炸，不能返回空列表 —— 空数据集会让评测「全部通过」。"""
    with pytest.raises(FileNotFoundError):
        load_cases(tmp_path / "nope")


def test_load_tolerates_a_hand_written_partial_case(tmp_path: Path) -> None:
    """样本是手改的，缺字段要按缺省值读进来，而不是抛 KeyError。

    标注中途存盘、只写了一半的文件很常见；读取失败会让人不敢手改 JSON。
    """
    path = tmp_path / "partial.json"
    path.write_text(
        json.dumps({"id": "partial", "conversations": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    case = load_case(path)

    assert case.id == "partial"
    assert case.date == ""
    assert case.memory_before == {}
    assert case.expect == Expectation()
    # 缺输入、缺期望，validate 要把它拦下来
    assert case.validate()


# --------------------------------------------------------------- validate() 的判据


def test_validate_accepts_a_well_formed_case() -> None:
    assert _minimal_case().validate() == []


def test_validate_catches_no_op_with_facts() -> None:
    """no_op 却标了事实 —— 最常见的手滑：先按正例标完，回头改成 no_op 忘了清空。

    这两个字段互相矛盾，判分时会自相打架：反例判「写了就是错」，
    facts 判「没写就是漏」，同一次整理不可能同时满分。
    """
    case = EvalCase(
        **{
            **_minimal_case().__dict__,
            "expect": Expectation(facts=["用户搬到了广州"], no_op=True),
        }
    )

    problems = case.validate()

    assert any("no_op" in p for p in problems)


def test_validate_catches_no_op_with_corrections() -> None:
    """corrections 也算「期望写入」，同样不能和 no_op 并存。"""
    case = EvalCase(
        **{
            **_minimal_case().__dict__,
            "memory_before": {INDEX_PATH: "- 住在深圳"},
            "expect": Expectation(
                corrections=[Correction(stale="- 住在深圳", becomes="住在广州")],
                no_op=True,
            ),
        }
    )

    assert any("no_op" in p for p in case.validate())


def test_validate_catches_stale_text_absent_from_the_snapshot() -> None:
    """stale 片段必须真的在整理前的记忆里，否则「旧的有没有被改掉」判不了。

    这是标注 corrections 时最容易犯的错：凭印象写一句大意相同的话，
    而不是从快照里原样复制。判分靠子串匹配，差一个字就永远判成「已改掉」，
    于是新旧并存这种最糟的失败会被静默放过。
    """
    case = EvalCase(
        **{
            **_minimal_case().__dict__,
            "expect": Expectation(
                corrections=[Correction(stale="- 住在深圳南山", becomes="住在广州")]
            ),
        }
    )

    problems = case.validate()

    assert any("stale" in p for p in problems)


def test_validate_matches_stale_across_all_snapshot_files() -> None:
    """stale 可以出现在快照里的任意一个文件，不限于索引。"""
    case = EvalCase(
        **{
            **_minimal_case().__dict__,
            "memory_before": {
                INDEX_PATH: "# 记忆索引\n\n- [基本情况](profile/basics.md) — 居住与工作\n",
                "/memories/profile/basics.md": "# 基本情况\n\n- 住在深圳南山\n",
            },
            "expect": Expectation(
                corrections=[Correction(stale="- 住在深圳南山", becomes="住在广州")]
            ),
        }
    )

    assert case.validate() == []


def test_validate_catches_a_case_with_nothing_to_score() -> None:
    """既不是 no_op、又没标任何期望的样本判不了分，等于白标。"""
    case = EvalCase(**{**_minimal_case().__dict__, "expect": Expectation()})

    assert any("判不了分" in p for p in case.validate())


def test_validate_catches_a_case_without_input() -> None:
    """没有对话就没有输入，这条样本重放不了。"""
    case = EvalCase(**{**_minimal_case().__dict__, "conversations": []})

    assert any("没有对话" in p for p in case.validate())
