"""技能的磁盘视图：扫描、读取、删除。

**磁盘是「有哪些技能」的唯一事实来源**，数据库只存「哪个被停用了、从哪装的」
（见 app/db/models.py 的 Skill）。这样把一个技能目录直接拷进来就能用，
不需要经过安装接口 —— 技能本来就该像 CLAUDE.md 一样是可以手写的东西。

全是同步文件 IO，调用方一律走 `asyncio.to_thread`。
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.skills.errors import InvalidSkillManifest, SkillError, SkillNotFound
from app.skills.manifest import (
    BODY_BUDGET,
    DESCRIPTION_BUDGET,
    SkillManifest,
    parse_skill_md,
)
from app.skills.paths import READABLE_SUFFIXES, normalize_name, resolve_in_skill

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "SKILL.md"
# 单个附带文件读进上下文的上限。超过就截断并告诉模型用 view_range 继续读，
# 而不是让一份 2MB 的 CSV 一次性挤爆上下文窗口。
MAX_FILE_CHARS = 20_000
# 列目录时最多列几个文件。技能包偶尔会带整个数据集，全列出来毫无信息量
MAX_LISTED_FILES = 100


@dataclass(frozen=True)
class SkillEntry:
    """扫描到的一个技能。

    ``error`` 非空表示这个目录有 SKILL.md 但解析失败。**仍然要列出来** ——
    让坏掉的技能在界面上凭空消失，人只会以为安装没成功然后再装一遍。
    坏掉的技能不会进 system prompt，也不能被 skill_read 读到。
    """

    name: str
    description: str = ""
    version: str = ""
    license: str = ""
    allowed_tools: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    size_bytes: int = 0
    error: str = ""
    # 装得上、但有值得说一句的问题（目前只有 description 超预算）。
    # 和 error 的区别是**它不影响可见性** —— 技能照常进 system prompt。
    # 存在的理由：这类问题只有人能修，而人看不见就等于不存在
    warning: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class SkillStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # ---------- 扫描 ----------

    def list(self) -> list[SkillEntry]:
        """列出技能目录下的全部技能，按名字排序。

        目录不存在不是错误 —— 一次都没装过技能就是这个状态。
        """
        if not self.root.is_dir():
            return []

        entries: list[SkillEntry] = []
        for child in sorted(self.root.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if not (child / MANIFEST_FILENAME).is_file():
                continue
            entries.append(self._entry(child))
        return entries

    def _entry(self, directory: Path) -> SkillEntry:
        try:
            name = normalize_name(directory.name)
        except SkillError as exc:
            return SkillEntry(name=directory.name, error=str(exc))

        try:
            manifest = self._manifest(directory, name)
        except SkillError as exc:
            return SkillEntry(name=name, error=str(exc))

        files, size = _walk(directory)
        return SkillEntry(
            name=name,
            description=manifest.description,
            version=manifest.version,
            license=manifest.license,
            allowed_tools=manifest.allowed_tools,
            files=files,
            size_bytes=size,
            warning=_budget_warning(manifest),
        )

    def _manifest(self, directory: Path, name: str) -> SkillManifest:
        path = directory / MANIFEST_FILENAME
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidSkillManifest(f"SKILL.md 不是 UTF-8 文本：{exc}") from exc
        except OSError as exc:
            raise InvalidSkillManifest(f"读不到 SKILL.md：{exc}") from exc
        return parse_skill_md(text, expected_name=name)

    # ---------- 读取 ----------

    def manifest(self, name: str) -> SkillManifest:
        directory = resolve_in_skill(self.root, name)
        if not (directory / MANIFEST_FILENAME).is_file():
            raise SkillNotFound(f"没有装名为 {name!r} 的技能")
        return self._manifest(directory, normalize_name(name))

    def read_file(
        self, name: str, rel_path: str, view_range: list[int] | None = None
    ) -> str:
        """读技能目录里的一个附带文件，带行号（和 kb_read 一致的呈现）。"""
        target = resolve_in_skill(self.root, name, rel_path)
        if not target.is_file():
            raise SkillNotFound(f"技能 {name} 里没有 {rel_path}")
        if target.suffix.lower() not in READABLE_SUFFIXES:
            raise SkillError(
                f"{rel_path} 是二进制或未知格式（{target.suffix or '无后缀'}），读不出文本。"
                f"可读的后缀：{'、'.join(sorted(READABLE_SUFFIXES))}"
            )
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SkillError(f"{rel_path} 不是 UTF-8 文本：{exc}") from exc

        lines = text.splitlines()
        start, end = _range(view_range, len(lines))
        selected = lines[start - 1:end]
        rendered = "\n".join(
            f"{start + offset:>5}\t{line}" for offset, line in enumerate(selected)
        )
        if len(rendered) > MAX_FILE_CHARS:
            rendered = rendered[:MAX_FILE_CHARS]
            rendered += (
                f"\n\n（已截断，文件共 {len(lines)} 行；"
                "需要后面的内容请用 view_range 继续读）"
            )
        return rendered or f"{rel_path} 是空文件"

    def files(self, name: str) -> tuple[str, ...]:
        directory = resolve_in_skill(self.root, name)
        if not directory.is_dir():
            raise SkillNotFound(f"没有装名为 {name!r} 的技能")
        return _walk(directory)[0]

    # ---------- 删除 ----------

    def remove(self, name: str) -> None:
        directory = resolve_in_skill(self.root, name)
        if not directory.is_dir():
            raise SkillNotFound(f"没有装名为 {name!r} 的技能")
        shutil.rmtree(directory)
        logger.warning("✁ 删除技能 %s", name)


def _budget_warning(manifest: SkillManifest) -> str:
    """超预算但装得上的问题。两条预算的性质不同，所以措辞也不同。"""
    notes = []
    if len(manifest.description) > DESCRIPTION_BUDGET:
        notes.append(
            f"description 有 {len(manifest.description)} 字，超过 "
            f"{DESCRIPTION_BUDGET} 的建议预算 —— 它每轮对话都进 system prompt"
        )
    if len(manifest.body) > BODY_BUDGET:
        notes.append(
            f"正文有 {len(manifest.body)} 字符，超过 {BODY_BUDGET} 的建议预算 —— "
            "模型 skill_read 一次就要吃掉这么多上下文"
        )
    return "；".join(notes)


def _walk(directory: Path) -> tuple[tuple[str, ...], int]:
    """列出技能目录里除 SKILL.md 之外的文件，以及整个技能占的字节数。"""
    files: list[str] = []
    total = 0
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(directory).as_posix()
        if any(segment.startswith(".") for segment in rel.split("/")):
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
        if rel != MANIFEST_FILENAME:
            files.append(rel)
    return tuple(files[:MAX_LISTED_FILES]), total


def _range(view_range: list[int] | None, total: int) -> tuple[int, int]:
    if not view_range:
        return 1, total
    if len(view_range) != 2 or not all(isinstance(v, int) for v in view_range):
        raise SkillError("view_range 必须是 [起始行, 结束行] 两个整数")
    start, end = view_range
    if start < 1:
        raise SkillError("view_range 的起始行从 1 开始")
    return start, total if end == -1 else min(end, total)
