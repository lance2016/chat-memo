"""对 vault 的无状态扫描。

不建索引：个人 vault 几千个文件的现场遍历只要几十毫秒，而 vault 被 Obsidian
在外部随时改动，任何索引都要解决失效问题 —— 不维护索引就没有失效。
全部是同步实现，调用方（tool.py）用 asyncio.to_thread 包起来，别阻塞事件循环。
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.kb.errors import KbToolError
from app.kb.paths import READABLE_SUFFIXES, resolve_in_vault

# 读给模型的单文件上限；search 扫描时超过 2MB 的文件直接跳过
MAX_READ_BYTES = 1_000_000
MAX_SCAN_BYTES = 2_000_000
SNIPPET_LIMIT = 120

# [[目标]] / [[目标|别名]] / [[目标#标题]]，目标里不含 ] | #
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")


@dataclass(frozen=True)
class Hit:
    path: str  # vault 相对路径
    snippet: str  # 命中行，去首尾空白并截断
    mtime: float


def search(
    vault_root: str, query: str, path_prefix: str = "", limit: int = 20
) -> list[Hit]:
    """大小写不敏感的子串搜索。文件名命中排前，内容命中按 mtime 倒序。

    ``tag:#xxx``（或 ``tag:xxx``）只匹配标签：行内 ``#xxx`` 和 frontmatter 里的裸 tag。
    """
    query = query.strip()
    if not query:
        raise KbToolError("query 不能为空")
    if limit < 1:
        raise KbToolError("limit 必须大于 0")

    vault = resolve_in_vault(vault_root, "")
    start = resolve_in_vault(vault_root, path_prefix) if path_prefix else vault
    if not start.is_dir():
        raise KbToolError(f"目录 {path_prefix} 不存在")

    tag = ""
    if query.lower().startswith("tag:"):
        tag = query[len("tag:") :].lstrip("#").strip()
        if not tag:
            raise KbToolError("tag: 后面要跟标签名")
    needle = query.lower()

    name_hits: list[Hit] = []
    content_hits: list[Hit] = []
    for rel, full in _iter_notes(vault, start):
        lines = _read_lines(full, MAX_SCAN_BYTES)
        if lines is None:
            continue
        mtime = full.stat().st_mtime

        if tag:
            matched = _match_tag(lines, tag)
            if matched is not None:
                content_hits.append(Hit(rel, _snippet(matched), mtime))
            continue

        if needle in Path(rel).name.lower():
            first = next((line for line in lines if line.strip()), "")
            name_hits.append(Hit(rel, _snippet(first), mtime))
            continue
        matched = next((line for line in lines if needle in line.lower()), None)
        if matched is not None:
            content_hits.append(Hit(rel, _snippet(matched), mtime))

    name_hits.sort(key=lambda h: h.mtime, reverse=True)
    content_hits.sort(key=lambda h: h.mtime, reverse=True)
    return (name_hits + content_hits)[:limit]


def read_note(
    vault_root: str, rel_path: str, view_range: list[int] | None = None
) -> str:
    """带行号读一篇笔记，输出格式与 MemoryStore.view 一致，模型两边看到的是同一种样子。"""
    if not rel_path:
        raise KbToolError("path 不能为空")
    file = resolve_in_vault(vault_root, rel_path)
    if file.is_dir():
        raise KbToolError(f"{rel_path} 是目录，用 kb_list 查看")
    if not file.is_file():
        raise KbToolError(f"{rel_path} 不存在")
    if file.suffix.lower() not in READABLE_SUFFIXES:
        raise KbToolError(
            f"只支持读取 {'/'.join(sorted(READABLE_SUFFIXES))} 文件"
        )
    if view_range is None and file.stat().st_size > MAX_READ_BYTES:
        raise KbToolError(
            f"{rel_path} 超过 {MAX_READ_BYTES} 字节，请用 view_range 分段读"
        )

    lines = file.read_text(encoding="utf-8", errors="replace").split("\n")
    start, end = 1, len(lines)
    if view_range is not None:
        if len(view_range) != 2:
            raise KbToolError("view_range 必须是 [start, end] 两个整数")
        start, end = view_range
        if end == -1:
            end = len(lines)
        if start < 1 or start > len(lines) or end < start:
            raise KbToolError(f"view_range {view_range} 越界，文件共 {len(lines)} 行")
        end = min(end, len(lines))

    width = len(str(end))
    body = "\n".join(
        f"{i:>{width}}\t{line}"
        for i, line in enumerate(lines[start - 1 : end], start=start)
    )
    return f"{rel_path}:\n{body}"


def list_dir(vault_root: str, rel_path: str = "") -> str:
    """一层目录列表。只列可读笔记和子目录，附件汇总成一行计数。"""
    directory = resolve_in_vault(vault_root, rel_path)
    if directory.is_file():
        raise KbToolError(f"{rel_path} 是文件，用 kb_read 读取")
    if not directory.is_dir():
        raise KbToolError(f"目录 {rel_path or '/'} 不存在")

    entries: list[str] = []
    skipped = 0
    for child in sorted(directory.iterdir(), key=lambda p: p.name):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            count = sum(1 for c in child.iterdir() if not c.name.startswith("."))
            entries.append(f"{child.name}/  ({count} 项)")
        elif child.suffix.lower() in READABLE_SUFFIXES and not child.is_symlink():
            entries.append(f"{child.name}  ({child.stat().st_size} bytes)")
        else:
            skipped += 1

    listing = "\n".join(entries) if entries else "（空目录）"
    if skipped:
        listing += f"\n（另有 {skipped} 个非文本附件未列出）"
    return f"{rel_path or '/'} 目录内容：\n{listing}"


def backlinks(vault_root: str, rel_path: str) -> list[Hit]:
    """哪些笔记通过 ``[[双链]]`` 引用了这一篇，按 mtime 倒序。

    Obsidian 的链接目标通常是不带扩展名的文件名，也可能带路径或别名，
    所以按「最后一段的 stem」匹配。
    """
    if not rel_path:
        raise KbToolError("path 不能为空")
    vault = resolve_in_vault(vault_root, "")
    target = resolve_in_vault(vault_root, rel_path)
    if not target.is_file():
        raise KbToolError(f"{rel_path} 不存在")
    stem = target.stem.lower()

    hits: list[Hit] = []
    for rel, full in _iter_notes(vault, vault):
        if full == target:
            continue
        lines = _read_lines(full, MAX_SCAN_BYTES)
        if lines is None:
            continue
        for line in lines:
            if any(_link_stem(m) == stem for m in _WIKILINK.findall(line)):
                hits.append(Hit(rel, _snippet(line), full.stat().st_mtime))
                break
    hits.sort(key=lambda h: h.mtime, reverse=True)
    return hits


# ---------- 内部辅助 ----------


def _iter_notes(vault: Path, start: Path) -> Iterator[tuple[str, Path]]:
    """按路径序遍历可读笔记。跳过点目录和符号链接 —— 链接目标可能在 vault 外。"""
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if (
                name.startswith(".")
                or Path(name).suffix.lower() not in READABLE_SUFFIXES
                or full.is_symlink()
            ):
                continue
            yield full.relative_to(vault).as_posix(), full


def _read_lines(file: Path, max_bytes: int) -> list[str] | None:
    """读不了（太大 / 消失了 / 权限）就跳过，扫描不该因单个文件中断。"""
    try:
        if file.stat().st_size > max_bytes:
            return None
        return file.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return None


def _match_tag(lines: list[str], tag: str) -> str | None:
    """行内 ``#tag``，或 frontmatter 块里的裸 tag（tags: 字段的值不带 #）。"""
    inline = re.compile(rf"#{re.escape(tag)}(?![\w/\-])")
    bare = re.compile(rf"(?<![\w/\-]){re.escape(tag)}(?![\w/\-])")
    in_frontmatter = bool(lines) and lines[0].strip() == "---"
    for i, line in enumerate(lines):
        if in_frontmatter and i > 0 and line.strip() == "---":
            in_frontmatter = False
        if inline.search(line):
            return line
        if in_frontmatter and i > 0 and bare.search(line):
            return line
    return None


def _link_stem(link_target: str) -> str:
    """[[Projects/chat-memo|别名]] → chat-memo。"""
    last = link_target.strip().rsplit("/", 1)[-1]
    return last.removesuffix(".md").lower()


def _snippet(line: str) -> str:
    flat = line.strip()
    return flat if len(flat) <= SNIPPET_LIMIT else flat[:SNIPPET_LIMIT] + "…"
