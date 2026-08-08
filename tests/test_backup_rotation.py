"""自动备份的补跑判定与轮换。

这块的设计要点是**不建表**：dump 的文件名里带日期，备份目录本身就是「备份过没有」
那份记录。少一张表、少一次迁移，也不会出现「表说备份过了但文件被删了」的假象。
"""

import datetime as dt
from pathlib import Path

from app.backup import DUMP_PREFIX, DUMP_SUFFIX, dump_day, dump_files, is_due, prune


def _make(directory: Path, stamp: str) -> Path:
    path = directory / f"{DUMP_PREFIX}{stamp}{DUMP_SUFFIX}"
    path.write_bytes(b"dump")
    return path


def test_due_when_nothing_backed_up_today(tmp_path: Path) -> None:
    _make(tmp_path, "20260801-040000")

    assert is_due(dt.date(2026, 8, 8), tmp_path) is True


def test_not_due_when_today_already_has_one(tmp_path: Path) -> None:
    """判据是「今天备份过没有」，不是「到点了没有」。

    定时触发在笔记本上必然漏（凌晨在睡眠），查询式则是睡醒就补 ——
    和 notify 的补跑式扫描同一条教训。
    """
    _make(tmp_path, "20260808-041500")

    assert is_due(dt.date(2026, 8, 8), tmp_path) is False


def test_due_on_an_empty_directory(tmp_path: Path) -> None:
    """第一次跑、或者备份目录被清过 —— 都该立刻补一份。"""
    assert is_due(dt.date(2026, 8, 8), tmp_path) is True


def test_unparseable_names_do_not_count_as_a_backup(tmp_path: Path) -> None:
    """手工放进来的文件不该让系统以为今天备份过了。"""
    (tmp_path / f"{DUMP_PREFIX}手工备份{DUMP_SUFFIX}").write_bytes(b"x")

    assert dump_day(tmp_path / f"{DUMP_PREFIX}手工备份{DUMP_SUFFIX}") is None
    assert is_due(dt.date(2026, 8, 8), tmp_path) is True


def test_prune_keeps_the_newest(tmp_path: Path) -> None:
    for stamp in ("20260801-040000", "20260802-040000", "20260803-040000"):
        _make(tmp_path, stamp)

    removed = prune(2, tmp_path)

    assert removed == [f"{DUMP_PREFIX}20260801-040000{DUMP_SUFFIX}"]
    assert [p.name for p in dump_files(tmp_path)] == [
        f"{DUMP_PREFIX}20260803-040000{DUMP_SUFFIX}",
        f"{DUMP_PREFIX}20260802-040000{DUMP_SUFFIX}",
    ]


def test_prune_never_deletes_everything(tmp_path: Path) -> None:
    """keep=0 是个配置事故，不该变成「把所有备份删光」。"""
    _make(tmp_path, "20260803-040000")

    prune(0, tmp_path)

    assert len(dump_files(tmp_path)) == 1


def test_prune_leaves_foreign_files_alone(tmp_path: Path) -> None:
    """备份目录里可能有别的东西（记忆导出目录、手工放的归档）。"""
    _make(tmp_path, "20260801-040000")
    _make(tmp_path, "20260802-040000")
    (tmp_path / "memories").mkdir()
    (tmp_path / "手工归档.tar.gz").write_bytes(b"x")

    prune(1, tmp_path)

    assert (tmp_path / "memories").exists()
    assert (tmp_path / "手工归档.tar.gz").exists()
