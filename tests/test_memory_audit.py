"""索引一致性校验。

这些用例钉住的是**渐进式披露的可见性前提**：索引漏一行，那条记忆就再也不会被读到，
而且不会有任何报错。校验放松一点点，这个静默失败就回来了。
"""

from app.db.models import Memory
from app.memory.audit import (
    MAX_DESCRIPTION_CHARS,
    audit_index,
    parse_index,
)


def _memory(path: str, content: str = "内容") -> Memory:
    return Memory(path=path, content=content)


def _index(*lines: str) -> Memory:
    return _memory("/memories/MEMORY.md", "# 记忆索引\n\n" + "\n".join(lines))


def test_consistent_index_has_no_issues() -> None:
    audit = audit_index(
        [
            _index(
                "- [身份](profile/identity.md) — 基本身份信息",
                "- [偏好](profile/preferences.md) — 常用工具与开发环境",
            ),
            _memory("/memories/profile/identity.md"),
            _memory("/memories/profile/preferences.md"),
        ]
    )

    assert audit.ok
    assert audit.total_files == 2
    assert audit.as_prompt() == ""


def test_file_without_index_entry_is_reported() -> None:
    """最严重的那一种：文件还在，但模型永远看不到它。"""
    audit = audit_index(
        [
            _index("- [身份](profile/identity.md) — 基本身份信息"),
            _memory("/memories/profile/identity.md"),
            _memory("/memories/projects/chat.md"),
        ]
    )

    assert audit.missing == ("/memories/projects/chat.md",)
    assert audit.orphaned == ()
    assert not audit.ok


def test_entry_without_file_is_reported() -> None:
    audit = audit_index(
        [
            _index("- [不存在](projects/gone.md) — 一个已经删掉的项目"),
        ]
    )

    assert audit.orphaned == ("/memories/projects/gone.md",)


def test_empty_file_counts_as_missing_not_present() -> None:
    """空文件等于没有内容，不该因为「行还在」就算索引有效。"""
    audit = audit_index(
        [
            _index("- [空的](projects/empty.md) — 占位"),
            _memory("/memories/projects/empty.md", "   \n"),
        ]
    )

    assert audit.orphaned == ("/memories/projects/empty.md",)
    assert audit.total_files == 0


def test_overlong_description_is_reported() -> None:
    """条目写成结论而不是主题，索引就退化成记忆的压缩版。"""
    conclusion = "uv 不用 pip；macOS + Ghostty；类型注解必写；提交用 Conventional Commits"
    audit = audit_index(
        [
            _index(f"- [偏好](profile/preferences.md) — {conclusion}"),
            _memory("/memories/profile/preferences.md"),
        ]
    )

    assert audit.overlong == (("/memories/profile/preferences.md", len(conclusion)),)
    assert len(conclusion) > MAX_DESCRIPTION_CHARS


def test_description_at_the_limit_passes() -> None:
    audit = audit_index(
        [
            _index("- [偏好](profile/preferences.md) — " + "字" * MAX_DESCRIPTION_CHARS),
            _memory("/memories/profile/preferences.md"),
        ]
    )

    assert audit.overlong == ()


def test_missing_index_with_memories_is_an_issue() -> None:
    audit = audit_index([_memory("/memories/profile/identity.md")])

    assert audit.index_missing is True
    assert not audit.ok


def test_empty_store_is_not_an_issue() -> None:
    """还没有任何记忆时不该催模型去建索引。"""
    assert audit_index([]).ok


def test_absolute_paths_in_entries_resolve() -> None:
    """模型有时写全路径，有时写相对路径，两种都得认。"""
    audit = audit_index(
        [
            _index("- [身份](/memories/profile/identity.md) — 基本身份信息"),
            _memory("/memories/profile/identity.md"),
        ]
    )

    assert audit.ok


def test_separator_variants_are_tolerated() -> None:
    """分隔符长什么样不影响索引能不能用，不为它报错。"""
    audit = audit_index(
        [
            _index(
                "- [一](a.md) — 破折号",
                "- [二](b.md) - 连字符",
                "- [三](c.md)：冒号",
            ),
            _memory("/memories/a.md"),
            _memory("/memories/b.md"),
            _memory("/memories/c.md"),
        ]
    )

    assert audit.ok


def test_prose_lines_are_not_flagged_as_malformed() -> None:
    """索引里的标题和说明文字不是坏条目。"""
    index = Memory(
        path="/memories/MEMORY.md",
        content=(
            "# 记忆索引\n\n"
            "下面每行一个记忆文件。\n\n"
            "## 个人\n"
            "- [身份](profile/identity.md) — 基本身份信息\n"
        ),
    )
    audit = audit_index([index, _memory("/memories/profile/identity.md")])

    assert audit.malformed == ()
    assert audit.ok


def test_bullet_that_looks_like_an_entry_but_is_not_a_link() -> None:
    entries, malformed = parse_index("- profile/identity.md 基本身份信息")

    assert entries == []
    assert malformed == [(1, "- profile/identity.md 基本身份信息")]


def test_path_escaping_memory_root_is_malformed_not_orphaned() -> None:
    """穿越出去的路径是格式问题，不是「文件不存在」—— 别让它变成一条催修的假记忆。"""
    audit = audit_index([_index("- [跑偏](../etc/passwd) — 不该出现")])

    assert audit.orphaned == ()
    assert len(audit.malformed) == 1


def test_prompt_lists_every_issue() -> None:
    """反馈回路的实际载荷：模型要能从这段话里知道该修什么。"""
    audit = audit_index(
        [
            _index("- [没了](projects/gone.md) — 已删除的项目"),
            _memory("/memories/projects/chat.md"),
        ]
    )
    prompt = audit.as_prompt()

    assert "/memories/projects/chat.md" in prompt
    assert "/memories/projects/gone.md" in prompt
    assert "缺条目" in prompt and "空条目" in prompt


def test_summary_reports_success_too() -> None:
    assert "通过" in audit_index([]).summary()
