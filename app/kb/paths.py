"""vault 路径校验。

kb 工具的 path 来自模型输出，而 vault 是真实文件系统 —— 除了字符串层面的规范化，
还必须做 realpath 遏制：vault 里可能有指向库外的符号链接，纯字符串检查拦不住
（memory/paths.py 不需要这层，因为记忆不落真实磁盘）。
"""

from __future__ import annotations

import posixpath
from pathlib import Path

from app.kb.errors import InvalidKbPath

MAX_PATH_LENGTH = 512
# 只读这几类文本笔记；附件（图片 / PDF）模型读不了，直接拒绝
READABLE_SUFFIXES = frozenset({".md", ".txt", ".canvas"})


def normalize_rel_path(raw: object) -> str:
    """规范化 vault 相对路径（纯字符串操作，不触碰文件系统）。

    空串是合法返回值，表示 vault 根目录 —— 只有 kb_list 用得到。
    """
    if not isinstance(raw, str):
        raise InvalidKbPath(f"path 必须是字符串，收到 {type(raw).__name__}")

    path = raw.strip().strip("/")
    if len(path) > MAX_PATH_LENGTH:
        raise InvalidKbPath(f"path 超过 {MAX_PATH_LENGTH} 字符")
    if "\x00" in path:
        raise InvalidKbPath("path 不能包含空字节")
    if "\\" in path:
        raise InvalidKbPath("path 只能用 / 作为分隔符")

    normalized = posixpath.normpath(path) if path else ""
    if normalized in (".", ""):
        return ""
    if normalized == ".." or normalized.startswith("../"):
        raise InvalidKbPath(f"path 不能越出 vault：{raw!r}")
    for segment in normalized.split("/"):
        if segment.startswith("."):
            raise InvalidKbPath("不能访问以 . 开头的文件或目录（.obsidian、.trash 等）")
    return normalized


def resolve_in_vault(vault_root: str, rel_path: str) -> Path:
    """把相对路径落到 vault 内的真实位置，防符号链接逃逸。"""
    vault = Path(vault_root).resolve()
    target = (vault / rel_path).resolve() if rel_path else vault
    if not target.is_relative_to(vault):
        raise InvalidKbPath(f"{rel_path} 经符号链接解析后越出了 vault")
    return target
