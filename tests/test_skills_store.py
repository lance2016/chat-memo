from pathlib import Path

import pytest

from app.skills.errors import InvalidSkillPath, SkillError, SkillNotFound
from app.skills.paths import normalize_name, normalize_rel_path, resolve_in_skill
from app.skills.store import SkillStore


def write_skill(root: Path, name: str, *, description: str = "做某件事时使用。",
                body: str = "步骤一。", files: dict[str, str] | None = None) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    for rel, content in (files or {}).items():
        target = directory / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return directory


def test_lists_skills_with_files(tmp_path: Path):
    write_skill(tmp_path, "alpha", files={"references/api.md": "# API"})
    write_skill(tmp_path, "beta")

    entries = SkillStore(tmp_path).list()

    assert [e.name for e in entries] == ["alpha", "beta"]
    assert entries[0].files == ("references/api.md",)
    assert entries[0].size_bytes > 0
    assert all(e.ok for e in entries)


def test_missing_root_is_not_an_error(tmp_path: Path):
    """一次都没装过技能就是这个状态，不该炸。"""
    assert SkillStore(tmp_path / "nope").list() == []


def test_directory_without_manifest_is_ignored(tmp_path: Path):
    (tmp_path / "not-a-skill").mkdir()
    (tmp_path / "not-a-skill" / "readme.md").write_text("hi", encoding="utf-8")

    assert SkillStore(tmp_path).list() == []


def test_broken_skill_is_listed_with_error(tmp_path: Path):
    """坏掉的技能必须仍然可见，否则人只会以为没装上然后再装一遍。"""
    directory = tmp_path / "broken"
    directory.mkdir()
    (directory / "SKILL.md").write_text("没有 frontmatter", encoding="utf-8")

    (entry,) = SkillStore(tmp_path).list()

    assert entry.name == "broken"
    assert not entry.ok
    assert "frontmatter" in entry.error


def test_read_file_renders_line_numbers_and_range(tmp_path: Path):
    write_skill(tmp_path, "alpha", files={"notes.md": "一\n二\n三\n四"})
    store = SkillStore(tmp_path)

    assert "1\t一" in store.read_file("alpha", "notes.md")
    ranged = store.read_file("alpha", "notes.md", [2, 3])
    assert "二" in ranged and "三" in ranged and "四" not in ranged


def test_binary_suffix_is_refused(tmp_path: Path):
    write_skill(tmp_path, "alpha", files={"logo.png": "not really a png"})

    with pytest.raises(SkillError, match="读不出文本"):
        SkillStore(tmp_path).read_file("alpha", "logo.png")


def test_unknown_skill_raises(tmp_path: Path):
    with pytest.raises(SkillNotFound):
        SkillStore(tmp_path).manifest("ghost")


def test_remove_deletes_directory(tmp_path: Path):
    write_skill(tmp_path, "alpha")
    SkillStore(tmp_path).remove("alpha")

    assert not (tmp_path / "alpha").exists()


# ---- 路径校验 ----


@pytest.mark.parametrize("raw", ["../etc", "a/b", "UPPER", "-lead", "trail-", "", "空 格"])
def test_bad_names_rejected(raw: str):
    with pytest.raises(InvalidSkillPath):
        normalize_name(raw)


@pytest.mark.parametrize("raw", ["../secrets", "a/../../b", ".hidden/x", "a\\b", ""])
def test_bad_rel_paths_rejected(raw: str):
    with pytest.raises(InvalidSkillPath):
        normalize_rel_path(raw)


def test_symlink_escaping_skill_is_rejected(tmp_path: Path):
    """压缩包能带软链；字符串层面的校验完全拦不住，必须做 realpath 遏制。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    root = tmp_path / "skills"
    write_skill(root, "alpha")
    (root / "alpha" / "leak.md").symlink_to(outside / "secret.md")

    with pytest.raises(InvalidSkillPath, match="越出"):
        resolve_in_skill(root, "alpha", "leak.md")


def test_symlinks_are_not_listed(tmp_path: Path):
    root = tmp_path / "skills"
    write_skill(root, "alpha")
    (root / "alpha" / "leak.md").symlink_to(tmp_path / "elsewhere.md")

    (entry,) = SkillStore(root).list()
    assert entry.files == ()
