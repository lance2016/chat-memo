"""索引一致性校验：记忆质量里代码能判定的那一部分。

渐进式披露的前提是「索引里有这个文件」。`MEMORY.md` 漏掉一行，那条记忆就实质性
死亡 —— 文件还躺在 `memories` 表里，但模型永远不会 `view` 它，因为它不知道有这个
文件。而索引现在**全靠提示词让模型自觉**维护（`jobs/prompts.CONSOLIDATE_PROMPT`
第 5 条），漏了没有任何东西会报错。这个模块就是那个会报错的东西。

三处用同一份结果，这是它值得存在的理由（见 docs/evaluation.md）：

1. **护栏** —— 每次整理跑完自检一遍，问题进日志和 `ConsolidationResult`
2. **反馈** —— 差异写进下次整理的 prompt，让模型自己修
3. **指标** —— 评测里最便宜、最无争议的那一个

**为什么只发现不自动修**：条目的描述文字要读懂文件内容才写得出来，代码补不出来。
补一行占位的比缺一行更糟 —— 缺一行会被下次校验抓到，占位的不会。
代码负责发现，模型负责修复。
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from app.db.models import Memory
from app.memory.paths import INDEX_PATH, MEMORY_ROOT

# 和 memory/prompt.py 里写给模型的上限对齐。改这里要同时改那边的提示词，
# 否则校验会一直报模型按提示词写出来的正确条目。
MAX_DESCRIPTION_CHARS = 25

# 索引条目：`- [标题](相对路径) — 主题范围`
_ENTRY_RE = re.compile(
    r"^\s*[-*+]\s+\[(?P<title>[^\]]*)\]\((?P<path>[^)]*)\)(?P<rest>.*)$"
)
# 破折号写法在模型输出里有好几种，一律容忍 —— 分隔符长什么样不影响索引能不能用。
_SEPARATOR_RE = re.compile(r"^[\s—–—\-:：·|]+")


@dataclass(frozen=True)
class IndexEntry:
    """索引里的一行，路径已解析成绝对路径。"""

    title: str
    path: str
    description: str
    line: int


@dataclass(frozen=True)
class IndexAudit:
    """一次校验的全部结果。空的四个元组 = 索引和记忆完全对得上。"""

    # 有记忆文件，索引里没有对应条目 —— 这条记忆模型看不见了，最严重
    missing: tuple[str, ...] = ()
    # 索引指向的文件不存在 —— 模型 view 会扑空，白费一轮工具调用
    orphaned: tuple[str, ...] = ()
    # (路径, 实际字数)。索引退化成记忆的压缩版，见 memory/prompt.py
    overlong: tuple[tuple[str, int], ...] = ()
    # (行号, 原文)。像条目但解析不出来，说明模型没按格式走
    malformed: tuple[tuple[int, str], ...] = ()
    # 有记忆但连 MEMORY.md 都不存在。空记忆库不算问题
    index_missing: bool = False
    # 参与校验的记忆文件数（不含索引本身），用于解读上面几个数字的分母
    total_files: int = 0

    @property
    def issue_count(self) -> int:
        return (
            len(self.missing)
            + len(self.orphaned)
            + len(self.overlong)
            + len(self.malformed)
            + int(self.index_missing)
        )

    @property
    def ok(self) -> bool:
        return self.issue_count == 0

    def summary(self) -> str:
        """一行日志。ok 时也返回内容 —— 「校验通过」本身就是要看的信息。"""
        if self.ok:
            return f"索引校验通过（{self.total_files} 个记忆文件）"
        parts = []
        if self.index_missing:
            parts.append("索引文件缺失")
        if self.missing:
            parts.append(f"{len(self.missing)} 个文件没进索引")
        if self.orphaned:
            parts.append(f"{len(self.orphaned)} 条索引指向不存在的文件")
        if self.overlong:
            parts.append(f"{len(self.overlong)} 条描述超长")
        if self.malformed:
            parts.append(f"{len(self.malformed)} 行格式不对")
        return "索引校验：" + "、".join(parts)

    def as_prompt(self) -> str:
        """喂给下一次整理的问题清单。没问题时返回空串，整段不出现。

        写成祈使句而不是报告 —— 这段是给模型的任务，不是给人看的诊断。
        """
        if self.ok:
            return ""
        lines = [
            f"另外，上次整理后检查出以下索引问题，"
            f"**即使这天没有值得沉淀的内容也要修掉**（{INDEX_PATH}）："
        ]
        if self.index_missing:
            lines.append(f"- {INDEX_PATH} 不存在，需要创建，并为每个记忆文件写一行条目")
        for path in self.missing:
            lines.append(f"- 缺条目：{path} 有内容但索引里没有它，先 view 再补一行")
        for path in self.orphaned:
            lines.append(f"- 空条目：索引指向 {path}，但这个文件不存在，删掉这行或改成正确路径")
        for path, length in self.overlong:
            lines.append(
                f"- 描述超长：{path} 的条目 {length} 字，"
                f"改到 {MAX_DESCRIPTION_CHARS} 字以内，只写主题范围不写结论"
            )
        for line_no, raw in self.malformed:
            lines.append(f"- 格式不对：第 {line_no} 行 {raw!r}，改成 `- [标题](相对路径) — 主题范围`")
        return "\n".join(lines)


def parse_index(content: str) -> tuple[list[IndexEntry], list[tuple[int, str]]]:
    """把 MEMORY.md 解析成条目列表和解析不了的行。

    只有**看起来是条目**的行才会被判为 malformed：列表符号开头且提到了 `.md`。
    索引里的标题、说明文字、分组小标题都不该被当成坏条目报出来。
    """
    entries: list[IndexEntry] = []
    malformed: list[tuple[int, str]] = []

    for line_no, raw in enumerate(content.splitlines(), start=1):
        line = raw.rstrip()
        match = _ENTRY_RE.match(line)
        if match is None:
            stripped = line.strip()
            if stripped.startswith(("-", "*", "+")) and ".md" in stripped:
                malformed.append((line_no, stripped))
            continue

        path = _resolve(match.group("path"))
        if not path:
            malformed.append((line_no, line.strip()))
            continue

        entries.append(
            IndexEntry(
                title=match.group("title").strip(),
                path=path,
                description=_SEPARATOR_RE.sub("", match.group("rest")).strip(),
                line=line_no,
            )
        )
    return entries, malformed


def audit_index(memories: list[Memory]) -> IndexAudit:
    """对一份记忆快照做校验。纯函数 —— 不查库，所以评测里可以随便重放。"""
    index = next((m for m in memories if m.path == INDEX_PATH), None)
    files = {m.path for m in memories if m.path != INDEX_PATH and m.content.strip()}

    if index is None:
        # 空记忆库还没到该有索引的时候，不报问题。
        return IndexAudit(index_missing=bool(files), total_files=len(files))

    entries, malformed = parse_index(index.content)
    indexed = {entry.path for entry in entries}

    # 同一个文件被写了两行的情况不报错：重复条目只是难看，不影响可见性，
    # 而报出来会和 overlong 一起把 prompt 里的清单撑长，模型反而抓不住重点。
    return IndexAudit(
        missing=tuple(sorted(files - indexed)),
        orphaned=tuple(sorted(indexed - files)),
        overlong=tuple(
            sorted(
                (entry.path, len(entry.description))
                for entry in entries
                if len(entry.description) > MAX_DESCRIPTION_CHARS
            )
        ),
        malformed=tuple(malformed),
        total_files=len(files),
    )


def _resolve(raw: str) -> str:
    """把条目里的链接解析成 /memories 下的绝对路径，解析不了返回空串。"""
    path = raw.strip().split("#")[0].split(" ")[0].strip()
    if not path or "://" in path:
        return ""
    if not path.startswith("/"):
        path = f"{MEMORY_ROOT}/{path}"
    normalized = posixpath.normpath(path)
    # normpath 消解掉 .. 之后仍在 /memories 外的，视作解析失败而不是孤儿条目 ——
    # 那是格式问题，不是「文件不存在」。
    if not normalized.startswith(MEMORY_ROOT + "/"):
        return ""
    return normalized
