"""备份：记忆导出成真实文件树。

记忆平时只以数据库行存在，磁盘上没有 .md 文件；这里是唯一落成真实文件的地方，
所以路径穿越防护要重新验一遍。
"""

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.backup import export_memories
from app.db.models import Memory
from app.memory.store import MemoryStore


async def test_exports_tree_structure(session: AsyncSession, tmp_path: Path) -> None:
    store = MemoryStore(session, actor="chat")
    await store.create("/memories/MEMORY.md", "# 索引")
    await store.create("/memories/profile/preferences.md", "- 用 uv")
    await store.create("/memories/people/friend.md", "朋友")
    await session.commit()

    count = await export_memories(session, tmp_path / "memories")

    dest = tmp_path / "memories"
    assert count == 3
    assert (dest / "MEMORY.md").read_text() == "# 索引"
    assert (dest / "profile" / "preferences.md").read_text() == "- 用 uv"
    assert (dest / "people" / "friend.md").exists()


async def test_export_is_a_full_rewrite(session: AsyncSession, tmp_path: Path) -> None:
    """数据库里删掉的记忆不该在导出目录里阴魂不散。"""
    store = MemoryStore(session, actor="chat")
    await store.create("/memories/a.md", "1")
    await store.create("/memories/b.md", "2")
    await session.commit()
    await export_memories(session, tmp_path / "memories")

    await store.delete("/memories/b.md")
    await session.commit()
    await export_memories(session, tmp_path / "memories")

    assert (tmp_path / "memories" / "a.md").exists()
    assert not (tmp_path / "memories" / "b.md").exists()


async def test_export_content_is_verbatim(
    session: AsyncSession, tmp_path: Path
) -> None:
    body = "# 标题\n\n- 第一条\n- 第二条\n"
    await MemoryStore(session, actor="chat").create("/memories/x.md", body)
    await session.commit()

    await export_memories(session, tmp_path / "memories")
    assert (tmp_path / "memories" / "x.md").read_text(encoding="utf-8") == body


async def test_malicious_path_cannot_escape(
    session: AsyncSession, tmp_path: Path
) -> None:
    """直接往表里塞一条越界路径（绕过 store 的校验），导出必须拦住。

    模拟的是「数据库被别的途径写入了脏数据」，因为这里是真的往文件系统写。
    """
    session.add(Memory(path="/memories/../../etc/passwd", content="坏"))
    session.add(Memory(path="/etc/shadow", content="更坏"))
    await MemoryStore(session, actor="chat").create("/memories/ok.md", "正常")
    await session.commit()

    dest = tmp_path / "memories"
    count = await export_memories(session, dest)

    assert count == 1  # 只有正常那条被导出
    assert (dest / "ok.md").exists()
    assert not (tmp_path.parent / "etc" / "passwd").exists()
    # 导出目录之外什么都没多出来
    assert {p.name for p in dest.rglob("*") if p.is_file()} == {"ok.md"}


async def test_empty_memories_produces_empty_dir(
    session: AsyncSession, tmp_path: Path
) -> None:
    dest = tmp_path / "memories"
    assert await export_memories(session, dest) == 0
    assert dest.is_dir()
