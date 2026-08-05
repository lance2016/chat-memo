"""记忆存储：一个以 Postgres 为后端的虚拟文件系统。

记忆逻辑上是 /memories 下的一棵文件树，物理上是 ``memories`` 表的行。目录不单独存，
由路径前缀推导出来。每次写入都会在 ``memory_versions`` 留一条不可变快照。
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Memory, MemoryRead, MemoryVersion
from app.memory.errors import (
    InvalidMemoryPath,
    MemoryNotFound,
    MemoryToolError,
)
from app.memory.paths import MEMORY_ROOT, validate_path

MAX_CONTENT_BYTES = 100_000


@dataclass(frozen=True)
class MemoryNode:
    path: str
    is_dir: bool
    size: int = 0


class MemoryStore:
    """六个记忆命令的实现。

    ``actor`` 会写进版本记录，用来区分是聊天中实时写的、每日整理写的，还是手工改的。
    """

    def __init__(
        self,
        session: AsyncSession,
        actor: str = "chat",
        conversation_id: int | None = None,
        track_reads: bool = True,
    ) -> None:
        self.session = session
        self.actor = actor
        self.conversation_id = conversation_id
        # 前端浏览记忆页不该算「模型用了这条记忆」，那会污染使用率统计。
        self.track_reads = track_reads

    # ---------- 内部辅助 ----------

    async def _get(self, path: str) -> Memory | None:
        result = await self.session.execute(select(Memory).where(Memory.path == path))
        return result.scalar_one_or_none()

    async def _require(self, path: str) -> Memory:
        memory = await self._get(path)
        if memory is None:
            raise MemoryNotFound(f"{path} 不存在")
        return memory

    async def _children_of(self, dir_path: str) -> list[Memory]:
        prefix = MEMORY_ROOT if dir_path == MEMORY_ROOT else dir_path
        stmt = (
            select(Memory)
            .where(Memory.path.startswith(prefix + "/"))
            .order_by(Memory.path)
        )
        return list((await self.session.execute(stmt)).scalars())

    def _record_version(
        self, memory_id: int | None, path: str, content: str, operation: str
    ) -> None:
        self.session.add(
            MemoryVersion(
                memory_id=memory_id,
                path=path,
                content=content,
                operation=operation,
                actor=self.actor,
            )
        )

    @staticmethod
    def _check_size(content: str) -> None:
        size = len(content.encode("utf-8"))
        if size > MAX_CONTENT_BYTES:
            raise MemoryToolError(
                f"内容 {size} 字节，超过单条记忆上限 {MAX_CONTENT_BYTES} 字节。"
                "请拆成多个更小的记忆文件。"
            )

    # ---------- 六个命令 ----------

    def _record_read(self, path: str, found: bool) -> None:
        if not self.track_reads:
            return
        self.session.add(
            MemoryRead(
                path=path,
                actor=self.actor,
                conversation_id=self.conversation_id,
                found=found,
            )
        )

    async def view(self, path: str, view_range: list[int] | None = None) -> str:
        target = validate_path(path)
        memory = await self._get(target)

        if memory is None:
            children = await self._children_of(target)
            self._record_read(target, found=bool(children))
            if not children:
                raise MemoryNotFound(f"{target} 不存在")
            return self._render_dir(target, children)

        self._record_read(target, found=True)

        lines = memory.content.split("\n")
        start, end = 1, len(lines)
        if view_range is not None:
            if len(view_range) != 2:
                raise MemoryToolError("view_range 必须是 [start, end] 两个整数")
            start, end = view_range
            if end == -1:
                end = len(lines)
            if start < 1 or start > len(lines) or end < start:
                raise MemoryToolError(
                    f"view_range {view_range} 越界，文件共 {len(lines)} 行"
                )
            end = min(end, len(lines))

        width = len(str(end))
        body = "\n".join(
            f"{i:>{width}}\t{line}"
            for i, line in enumerate(lines[start - 1 : end], start=start)
        )
        return f"{target}:\n{body}"

    def _render_dir(self, dir_path: str, children: list[Memory]) -> str:
        prefix_len = len(dir_path.rstrip("/")) + 1
        seen_dirs: set[str] = set()
        entries: list[str] = []
        for child in children:
            rest = child.path[prefix_len:]
            head, _, tail = rest.partition("/")
            if tail:
                if head not in seen_dirs:
                    seen_dirs.add(head)
                    entries.append(f"{head}/")
            else:
                entries.append(f"{head}  ({len(child.content)} chars)")
        listing = "\n".join(sorted(entries))
        return f"{dir_path}/ 目录内容：\n{listing}"

    async def create(self, path: str, file_text: str) -> str:
        target = validate_path(path)
        if target == MEMORY_ROOT:
            raise InvalidMemoryPath("不能把 /memories 本身当作文件写入")
        self._check_size(file_text)

        memory = await self._get(target)
        if memory is None:
            memory = Memory(path=target, content=file_text)
            self.session.add(memory)
            await self.session.flush()
            self._record_version(memory.id, target, file_text, "created")
            return f"已创建 {target}"

        memory.content = file_text
        await self.session.flush()
        self._record_version(memory.id, target, file_text, "modified")
        return f"已覆盖 {target}"

    async def str_replace(self, path: str, old_str: str, new_str: str) -> str:
        target = validate_path(path)
        memory = await self._require(target)

        occurrences = memory.content.count(old_str)
        if occurrences == 0:
            raise MemoryToolError(f"{target} 中找不到要替换的文本")
        if occurrences > 1:
            raise MemoryToolError(
                f"{target} 中出现 {occurrences} 次该文本，无法确定替换哪一处。"
                "请提供更长、唯一的 old_str。"
            )

        updated = memory.content.replace(old_str, new_str, 1)
        self._check_size(updated)
        memory.content = updated
        await self.session.flush()
        self._record_version(memory.id, target, updated, "modified")
        return f"已更新 {target}"

    async def insert(self, path: str, insert_line: int, insert_text: str) -> str:
        target = validate_path(path)
        memory = await self._require(target)

        lines = memory.content.split("\n")
        if insert_line < 0 or insert_line > len(lines):
            raise MemoryToolError(
                f"insert_line {insert_line} 越界，文件共 {len(lines)} 行（0 表示插到开头）"
            )

        new_lines = insert_text.split("\n")
        lines[insert_line:insert_line] = new_lines
        updated = "\n".join(lines)
        self._check_size(updated)

        memory.content = updated
        await self.session.flush()
        self._record_version(memory.id, target, updated, "modified")
        return f"已在 {target} 第 {insert_line} 行后插入 {len(new_lines)} 行"

    async def delete(self, path: str) -> str:
        target = validate_path(path)
        if target == MEMORY_ROOT:
            raise InvalidMemoryPath("不能删除 /memories 根目录")

        memory = await self._get(target)
        if memory is not None:
            self._record_version(memory.id, target, memory.content, "deleted")
            await self.session.delete(memory)
            await self.session.flush()
            return f"已删除 {target}"

        children = await self._children_of(target)
        if not children:
            raise MemoryNotFound(f"{target} 不存在")
        for child in children:
            self._record_version(child.id, child.path, child.content, "deleted")
        await self.session.execute(
            sa_delete(Memory).where(Memory.path.startswith(target + "/"))
        )
        await self.session.flush()
        return f"已删除目录 {target}（共 {len(children)} 条记忆）"

    async def rename(self, old_path: str, new_path: str) -> str:
        source = validate_path(old_path)
        dest = validate_path(new_path)
        if source == dest:
            raise MemoryToolError("新旧路径相同")
        if source == MEMORY_ROOT or dest == MEMORY_ROOT:
            raise InvalidMemoryPath("不能重命名 /memories 根目录")
        if dest.startswith(source + "/"):
            raise InvalidMemoryPath("不能把目录移动到它自己的子目录里")

        memory = await self._get(source)
        if memory is not None:
            if await self._get(dest) is not None:
                raise MemoryToolError(f"{dest} 已存在")
            self._record_version(memory.id, source, memory.content, "deleted")
            memory.path = dest
            await self.session.flush()
            self._record_version(memory.id, dest, memory.content, "created")
            return f"已将 {source} 重命名为 {dest}"

        children = await self._children_of(source)
        if not children:
            raise MemoryNotFound(f"{source} 不存在")
        for child in children:
            moved = dest + child.path[len(source) :]
            if await self._get(moved) is not None:
                raise MemoryToolError(f"目标位置 {moved} 已存在")
            self._record_version(child.id, child.path, child.content, "deleted")
            child.path = moved
            self._record_version(child.id, moved, child.content, "created")
        await self.session.flush()
        return f"已将目录 {source} 重命名为 {dest}（共 {len(children)} 条记忆）"

    # ---------- 供 API / prompt 组装使用 ----------

    async def read(self, path: str) -> Memory:
        """按路径取原始记录（不带行号，区别于给模型看的 ``view``）。"""
        return await self._require(validate_path(path))

    async def list_all(self) -> list[Memory]:
        stmt = select(Memory).order_by(Memory.path)
        return list((await self.session.execute(stmt)).scalars())

    async def tree(self) -> list[MemoryNode]:
        """扁平化的树，目录节点由路径前缀推导。"""
        memories = await self.list_all()
        nodes: dict[str, MemoryNode] = {}
        for memory in memories:
            parent = posixpath.dirname(memory.path)
            while parent and parent != MEMORY_ROOT and parent not in nodes:
                nodes[parent] = MemoryNode(path=parent, is_dir=True)
                parent = posixpath.dirname(parent)
            nodes[memory.path] = MemoryNode(
                path=memory.path, is_dir=False, size=len(memory.content)
            )
        return sorted(nodes.values(), key=lambda n: n.path)
