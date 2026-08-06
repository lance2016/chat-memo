import os
from pathlib import Path

import pytest

from app.kb.errors import KbToolError
from app.kb.search import backlinks, list_dir, read_note, search


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "Projects").mkdir(parents=True)
    (root / "Areas").mkdir()
    (root / ".obsidian").mkdir()
    (root / "attachments").mkdir()

    (root / "Projects" / "chat-memo.md").write_text(
        "# chat-memo\n\n用 FastAPI 和 Postgres 写的个人助手 #dev\n参考 [[DeepSeek]] 的文档\n"
    )
    (root / "Projects" / "roadmap.md").write_text(
        "- [[chat-memo|助手项目]] 下一步：完善 FastAPI 路由\n"
    )
    (root / "Areas" / "reading.md").write_text(
        "---\ntags: [book]\n---\n\n最近在读的书\n参见 [[Projects/chat-memo#架构]]\n"
    )
    (root / "DeepSeek.md").write_text("DeepSeek 模型笔记\n")
    (root / ".obsidian" / "hidden.md").write_text("FastAPI 不该被搜到")
    (root / "attachments" / "img.png").write_bytes(b"\x89PNG")

    # 固定 mtime，让排序断言不依赖文件创建的先后耗时
    os.utime(root / "Projects" / "chat-memo.md", (1000, 1000))
    os.utime(root / "Projects" / "roadmap.md", (2000, 2000))
    return root


# ---------- search ----------


def test_search_matches_content_case_insensitive(vault: Path) -> None:
    hits = search(str(vault), "fastapi")
    paths = [h.path for h in hits]
    assert "Projects/chat-memo.md" in paths
    assert "Projects/roadmap.md" in paths
    # 点目录里的文件不该出现
    assert all(".obsidian" not in p for p in paths)


def test_content_hits_sorted_by_mtime_desc(vault: Path) -> None:
    hits = search(str(vault), "fastapi")
    assert [h.path for h in hits] == ["Projects/roadmap.md", "Projects/chat-memo.md"]


def test_filename_hits_rank_first(vault: Path) -> None:
    # "deepseek" 同时命中文件名（DeepSeek.md）和内容（chat-memo.md 里的双链）
    hits = search(str(vault), "deepseek")
    assert hits[0].path == "DeepSeek.md"
    assert {h.path for h in hits[1:]} == {"Projects/chat-memo.md"}


def test_tag_query_inline(vault: Path) -> None:
    hits = search(str(vault), "tag:#dev")
    assert [h.path for h in hits] == ["Projects/chat-memo.md"]


def test_tag_query_frontmatter(vault: Path) -> None:
    hits = search(str(vault), "tag:book")
    assert [h.path for h in hits] == ["Areas/reading.md"]
    # 正文里的「书」字不该被 tag 搜索命中成别的文件
    assert search(str(vault), "tag:读") == []


def test_path_prefix_scopes_search(vault: Path) -> None:
    assert search(str(vault), "fastapi", path_prefix="Areas") == []
    hits = search(str(vault), "fastapi", path_prefix="Projects")
    assert len(hits) == 2


def test_limit_caps_results(vault: Path) -> None:
    assert len(search(str(vault), "fastapi", limit=1)) == 1


def test_search_rejects_bad_input(vault: Path) -> None:
    with pytest.raises(KbToolError):
        search(str(vault), "   ")
    with pytest.raises(KbToolError):
        search(str(vault), "x", path_prefix="不存在的目录")
    with pytest.raises(KbToolError):
        search(str(vault), "tag:")


# ---------- backlinks ----------


def test_backlinks_matches_alias_and_path_links(vault: Path) -> None:
    hits = backlinks(str(vault), "Projects/chat-memo.md")
    # [[chat-memo|别名]] 和 [[Projects/chat-memo#标题]] 两种写法都算
    assert {h.path for h in hits} == {"Projects/roadmap.md", "Areas/reading.md"}


def test_backlinks_plain_link(vault: Path) -> None:
    hits = backlinks(str(vault), "DeepSeek.md")
    assert [h.path for h in hits] == ["Projects/chat-memo.md"]


def test_backlinks_missing_note(vault: Path) -> None:
    with pytest.raises(KbToolError):
        backlinks(str(vault), "nope.md")


# ---------- list_dir ----------


def test_list_root_hides_dot_dirs_and_counts_attachments(vault: Path) -> None:
    listing = list_dir(str(vault))
    assert "Projects/" in listing
    assert "DeepSeek.md" in listing
    assert ".obsidian" not in listing

    inner = list_dir(str(vault), "attachments")
    assert "1 个非文本附件" in inner


def test_list_rejects_file_and_missing(vault: Path) -> None:
    with pytest.raises(KbToolError):
        list_dir(str(vault), "DeepSeek.md")
    with pytest.raises(KbToolError):
        list_dir(str(vault), "nope")


# ---------- read_note ----------


def test_read_note_with_line_numbers(vault: Path) -> None:
    text = read_note(str(vault), "Projects/chat-memo.md")
    assert text.startswith("Projects/chat-memo.md:\n")
    assert "1\t# chat-memo" in text


def test_read_note_view_range(vault: Path) -> None:
    text = read_note(str(vault), "Projects/chat-memo.md", view_range=[3, 3])
    assert "FastAPI" in text
    assert "# chat-memo" not in text

    with pytest.raises(KbToolError):
        read_note(str(vault), "Projects/chat-memo.md", view_range=[99, 100])
    with pytest.raises(KbToolError):
        read_note(str(vault), "Projects/chat-memo.md", view_range=[1])


def test_read_note_rejects_missing_dir_and_binary(vault: Path) -> None:
    with pytest.raises(KbToolError):
        read_note(str(vault), "nope.md")
    with pytest.raises(KbToolError):
        read_note(str(vault), "Projects")
    with pytest.raises(KbToolError):
        read_note(str(vault), "attachments/img.png")
