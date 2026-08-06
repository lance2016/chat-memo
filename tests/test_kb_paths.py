import pytest

from app.kb.errors import InvalidKbPath
from app.kb.paths import normalize_rel_path, resolve_in_vault


def test_normalizes_slashes_and_whitespace() -> None:
    assert normalize_rel_path(" /Projects/chat-memo.md ") == "Projects/chat-memo.md"
    assert normalize_rel_path("a//b.md") == "a/b.md"
    assert normalize_rel_path("a/./b.md") == "a/b.md"


def test_empty_means_vault_root() -> None:
    assert normalize_rel_path("") == ""
    assert normalize_rel_path("/") == ""


def test_rejects_traversal() -> None:
    with pytest.raises(InvalidKbPath):
        normalize_rel_path("../etc/passwd")
    with pytest.raises(InvalidKbPath):
        normalize_rel_path("a/../../b.md")


def test_rejects_dot_segments() -> None:
    """.obsidian、.trash 这些 Obsidian 内部目录对模型不可见。"""
    with pytest.raises(InvalidKbPath):
        normalize_rel_path(".obsidian/app.json")
    with pytest.raises(InvalidKbPath):
        normalize_rel_path("notes/.trash/x.md")
    with pytest.raises(InvalidKbPath):
        normalize_rel_path("notes/.hidden.md")


def test_rejects_bad_input() -> None:
    with pytest.raises(InvalidKbPath):
        normalize_rel_path(123)
    with pytest.raises(InvalidKbPath):
        normalize_rel_path("a\x00b.md")
    with pytest.raises(InvalidKbPath):
        normalize_rel_path("a\\b.md")
    with pytest.raises(InvalidKbPath):
        normalize_rel_path("x" * 600)


def test_symlink_escape_is_blocked(tmp_path) -> None:
    """字符串校验拦不住符号链接，realpath 遏制必须兜底。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "secret.md"
    outside.write_text("外面的内容")
    (vault / "leak.md").symlink_to(outside)

    with pytest.raises(InvalidKbPath):
        resolve_in_vault(str(vault), "leak.md")


def test_symlink_inside_vault_is_allowed(tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "real.md").write_text("内容")
    (vault / "alias.md").symlink_to(vault / "real.md")

    resolved = resolve_in_vault(str(vault), "alias.md")
    assert resolved == (vault / "real.md").resolve()
