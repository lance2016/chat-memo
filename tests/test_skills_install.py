import io
import zipfile
from pathlib import Path

import pytest

from app.skills.errors import SkillInstallError
from app.skills.install import MAX_ENTRIES, install_zip, parse_source


def make_zip(entries: dict[str, str], *, symlinks: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
        for name in symlinks:
            info = zipfile.ZipInfo(name)
            # 0xA1FF << 16：S_IFLNK + 0777，git 和 zip 打软链时就是这个样子
            info.external_attr = 0xA1FF << 16
            archive.writestr(info, "/etc/passwd")
    return buffer.getvalue()


def skill_md(name: str, description: str = "做某件事时使用。") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n正文。\n"


# ---- 来源解析 ----


def test_parses_shorthand_repo():
    source = parse_source("anthropics/skills")
    assert source.zip_url == "https://codeload.github.com/anthropics/skills/zip/HEAD"
    assert source.subdir == ""


def test_parses_repo_with_subdir_and_ref():
    source = parse_source("anthropics/skills/document-skills/pdf@main")
    assert source.zip_url.endswith("/anthropics/skills/zip/main")
    assert source.subdir == "document-skills/pdf"
    assert source.ref == "main"


def test_parses_github_tree_url():
    source = parse_source("https://github.com/anthropics/skills/tree/main/artifacts")
    assert source.zip_url.endswith("/anthropics/skills/zip/main")
    assert source.subdir == "artifacts"


def test_parses_direct_zip_url():
    source = parse_source("https://example.com/pack.zip")
    assert source.zip_url == "https://example.com/pack.zip"
    assert source.subdir == ""


def test_rejects_garbage_source():
    with pytest.raises(SkillInstallError, match="看不懂"):
        parse_source("这是什么")


# ---- 安装 ----


def test_installs_single_skill(tmp_path: Path):
    payload = make_zip({"my-skill/SKILL.md": skill_md("my-skill"),
                        "my-skill/refs/a.md": "# A"})

    (installed,) = install_zip(payload, tmp_path).installed

    assert installed.name == "my-skill"
    assert not installed.replaced
    assert (tmp_path / "my-skill" / "SKILL.md").is_file()
    assert (tmp_path / "my-skill" / "refs" / "a.md").is_file()


def test_installs_every_skill_in_a_collection(tmp_path: Path):
    """anthropics/skills 这类合集仓库一个包里有多个 SKILL.md。"""
    payload = make_zip({
        "skills-main/pdf/SKILL.md": skill_md("pdf"),
        "skills-main/docx/SKILL.md": skill_md("docx"),
        "skills-main/README.md": "# 合集",
    })

    outcome = install_zip(payload, tmp_path)

    assert sorted(item.name for item in outcome.installed) == ["docx", "pdf"]
    assert outcome.skipped == ()


def test_subdir_selects_one_skill_from_a_collection(tmp_path: Path):
    payload = make_zip({
        "skills-main/pdf/SKILL.md": skill_md("pdf"),
        "skills-main/docx/SKILL.md": skill_md("docx"),
    })

    (installed,) = install_zip(payload, tmp_path, subdir="pdf").installed

    assert installed.name == "pdf"
    assert not (tmp_path / "docx").exists()


def test_overwrite_replaces_existing(tmp_path: Path):
    payload = make_zip({"a/SKILL.md": skill_md("a")})
    install_zip(payload, tmp_path)
    (tmp_path / "a" / "stale.md").write_text("旧文件", encoding="utf-8")

    (installed,) = install_zip(payload, tmp_path).installed

    assert installed.replaced
    # 覆盖是「换掉」，不是「合并」—— 残留旧文件会让人以为技能没更新
    assert not (tmp_path / "a" / "stale.md").exists()


def test_refuses_overwrite_when_disabled(tmp_path: Path):
    payload = make_zip({"a/SKILL.md": skill_md("a")})
    install_zip(payload, tmp_path)

    with pytest.raises(SkillInstallError, match="已经装过"):
        install_zip(payload, tmp_path, overwrite=False)


def test_rejects_package_without_manifest(tmp_path: Path):
    with pytest.raises(SkillInstallError, match="SKILL.md"):
        install_zip(make_zip({"readme.md": "# 什么都不是"}), tmp_path)


def test_invalid_skill_is_skipped_not_fatal(tmp_path: Path):
    """合集里一个不合规的不能把其余全挡在门外，但跳过了什么必须说出来。

    实测 anthropics/skills 的 18 个技能里就有不满足我们校验的。整批拒绝的实际
    结果是这个功能装不上任何真实技能。
    """
    payload = make_zip({
        "pack/good/SKILL.md": skill_md("good"),
        "pack/bad/SKILL.md": "没有 frontmatter",
    })

    outcome = install_zip(payload, tmp_path)

    assert [item.name for item in outcome.installed] == ["good"]
    assert [item.path for item in outcome.skipped] == ["pack/bad"]
    assert "frontmatter" in outcome.skipped[0].reason


def test_all_invalid_is_an_error(tmp_path: Path):
    payload = make_zip({"pack/bad/SKILL.md": "没有 frontmatter"})

    with pytest.raises(SkillInstallError, match="没有一个合法的技能"):
        install_zip(payload, tmp_path)


def test_installs_under_the_frontmatter_name(tmp_path: Path):
    """压缩包里那层目录叫什么是偶然的；frontmatter 的 name 才是技能的身份。

    官方合集里就有 template/ 装着 name: template-skill。用目录名当身份的话，
    这个技能永远装不上 —— 而它没有任何问题。
    """
    payload = make_zip({"template/SKILL.md": skill_md("template-skill")})

    (installed,) = install_zip(payload, tmp_path).installed

    assert installed.name == "template-skill"
    assert (tmp_path / "template-skill" / "SKILL.md").is_file()
    assert not (tmp_path / "template").exists()


def test_zip_slip_is_rejected(tmp_path: Path):
    payload = make_zip({"../../evil.md": "pwned", "a/SKILL.md": skill_md("a")})

    with pytest.raises(SkillInstallError, match="越界"):
        install_zip(payload, tmp_path)


def test_symlink_entries_are_skipped(tmp_path: Path):
    payload = make_zip({"a/SKILL.md": skill_md("a")}, symlinks=("a/leak.md",))

    install_zip(payload, tmp_path)

    assert not (tmp_path / "a" / "leak.md").exists()


def test_too_many_entries_rejected(tmp_path: Path):
    payload = make_zip({f"pack/f{i}.txt": "x" for i in range(MAX_ENTRIES + 1)})

    with pytest.raises(SkillInstallError, match="条目"):
        install_zip(payload, tmp_path)


def test_bad_zip_is_reported_clearly(tmp_path: Path):
    with pytest.raises(SkillInstallError, match="有效的 zip"):
        install_zip(b"not a zip at all", tmp_path)
