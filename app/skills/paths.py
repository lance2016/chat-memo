"""技能名和技能内相对路径的校验。

和 `app/kb/paths.py` 防的是同一类事故（模型输出的路径穿越 + 符号链接逃逸），
但多一层：技能是**从互联网下载**的，压缩包里的路径同样不可信 ——
`../../.ssh/authorized_keys` 这种条目要在解压时就被拒掉，不能等到读取时才拦。
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

from app.skills.errors import InvalidSkillPath

MAX_NAME_LENGTH = 64
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_PATH_LENGTH = 512
# 技能目录里可能有脚本和数据，但能读进上下文的只有文本。
# 二进制（图片、字体、模型权重）读出来只会是一堆乱码，直接拒绝更清楚。
READABLE_SUFFIXES = frozenset({
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv",
    ".py", ".js", ".ts", ".sh", ".sql", ".html", ".css", ".xml", ".ini",
})


def normalize_name(raw: object) -> str:
    """技能名：小写字母、数字和连字符。

    直接当目录名用，所以规则要比「看起来合理」更严 —— 大小写不敏感的文件系统上
    `Foo` 和 `foo` 是同一个目录，允许大写就等于允许两条记录抢同一块磁盘。
    """
    if not isinstance(raw, str):
        raise InvalidSkillPath(f"技能名必须是字符串，收到 {type(raw).__name__}")

    name = raw.strip().strip("/")
    if not name:
        raise InvalidSkillPath("技能名不能为空")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidSkillPath(f"技能名超过 {MAX_NAME_LENGTH} 字符")
    if not NAME_PATTERN.fullmatch(name):
        raise InvalidSkillPath(
            f"技能名 {raw!r} 只能包含小写字母、数字和连字符，且不能以连字符开头或结尾"
        )
    return name


def normalize_rel_path(raw: object) -> str:
    """技能目录内的相对路径。纯字符串操作，不碰文件系统。"""
    if not isinstance(raw, str):
        raise InvalidSkillPath(f"path 必须是字符串，收到 {type(raw).__name__}")

    path = raw.strip().strip("/")
    if not path:
        raise InvalidSkillPath("path 不能为空")
    if len(path) > MAX_PATH_LENGTH:
        raise InvalidSkillPath(f"path 超过 {MAX_PATH_LENGTH} 字符")
    if "\x00" in path:
        raise InvalidSkillPath("path 不能包含空字节")
    if "\\" in path:
        raise InvalidSkillPath("path 只能用 / 作为分隔符")

    normalized = posixpath.normpath(path)
    if normalized in (".", "..") or normalized.startswith("../"):
        raise InvalidSkillPath(f"path 不能越出技能目录：{raw!r}")
    for segment in normalized.split("/"):
        if segment.startswith("."):
            raise InvalidSkillPath("不能访问以 . 开头的文件或目录")
    return normalized


def resolve_in_skill(root: str | Path, name: str, rel_path: str = "") -> Path:
    """把技能内相对路径落到真实位置，并做 realpath 遏制。

    符号链接是必须拦的：压缩包能带软链，指向 `/etc/passwd` 的那种在字符串层面
    完全合法。解压时会跳过软链条目，这里是第二道 —— 手动放进目录的技能同样要过。
    """
    base = Path(root).resolve()
    skill_dir = (base / normalize_name(name)).resolve()
    if not skill_dir.is_relative_to(base):
        raise InvalidSkillPath(f"技能 {name} 经符号链接解析后越出了技能目录")

    if not rel_path:
        return skill_dir

    target = (skill_dir / normalize_rel_path(rel_path)).resolve()
    if not target.is_relative_to(skill_dir):
        raise InvalidSkillPath(f"{rel_path} 经符号链接解析后越出了技能 {name}")
    return target
