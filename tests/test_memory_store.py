import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Memory, MemoryVersion
from app.memory.errors import InvalidMemoryPath, MemoryNotFound, MemoryToolError
from app.memory.store import MAX_CONTENT_BYTES, MemoryStore


async def versions(session: AsyncSession) -> list[MemoryVersion]:
    stmt = select(MemoryVersion).order_by(MemoryVersion.id)
    return list((await session.execute(stmt)).scalars())


# ---------- create ----------


async def test_create_then_view(store: MemoryStore) -> None:
    await store.create("/memories/profile/identity.md", "我叫小明\n做 AI 应用开发")
    out = await store.view("/memories/profile/identity.md")
    assert "我叫小明" in out
    assert "1\t" in out  # 带行号


async def test_create_overwrites_and_versions(
    store: MemoryStore, session: AsyncSession
) -> None:
    await store.create("/memories/a.md", "v1")
    result = await store.create("/memories/a.md", "v2")
    assert "覆盖" in result

    assert (await store.view("/memories/a.md")).endswith("v2")
    ops = [v.operation for v in await versions(session)]
    assert ops == ["created", "modified"]


async def test_create_rejects_root(store: MemoryStore) -> None:
    with pytest.raises(InvalidMemoryPath):
        await store.create("/memories", "nope")


async def test_create_rejects_oversized(store: MemoryStore) -> None:
    with pytest.raises(MemoryToolError, match="超过单条记忆上限"):
        await store.create("/memories/big.md", "x" * (MAX_CONTENT_BYTES + 1))


# ---------- view ----------


async def test_view_missing_raises(store: MemoryStore) -> None:
    with pytest.raises(MemoryNotFound):
        await store.view("/memories/nope.md")


async def test_view_directory_lists_children(store: MemoryStore) -> None:
    await store.create("/memories/profile/identity.md", "a")
    await store.create("/memories/profile/preferences.md", "b")
    await store.create("/memories/profile/nested/deep.md", "c")

    out = await store.view("/memories/profile")
    assert "identity.md" in out
    assert "preferences.md" in out
    assert "nested/" in out  # 子目录折叠成一行
    assert "deep.md" not in out


async def test_view_range(store: MemoryStore) -> None:
    await store.create("/memories/a.md", "l1\nl2\nl3\nl4")
    out = await store.view("/memories/a.md", view_range=[2, 3])
    assert "l2" in out and "l3" in out
    assert "l1" not in out and "l4" not in out


async def test_view_range_to_end(store: MemoryStore) -> None:
    await store.create("/memories/a.md", "l1\nl2\nl3")
    out = await store.view("/memories/a.md", view_range=[2, -1])
    assert "l2" in out and "l3" in out and "l1" not in out


@pytest.mark.parametrize("bad", [[0, 2], [3, 1], [99, 100], [1], [1, 2, 3]])
async def test_view_range_invalid(store: MemoryStore, bad: list[int]) -> None:
    await store.create("/memories/a.md", "l1\nl2")
    with pytest.raises(MemoryToolError):
        await store.view("/memories/a.md", view_range=bad)


# ---------- str_replace ----------


async def test_str_replace(store: MemoryStore, session: AsyncSession) -> None:
    await store.create("/memories/a.md", "我用 pip 管理依赖")
    await store.str_replace("/memories/a.md", "pip", "uv")
    assert "我用 uv 管理依赖" in await store.view("/memories/a.md")
    assert [v.operation for v in await versions(session)] == ["created", "modified"]


async def test_str_replace_requires_unique_match(store: MemoryStore) -> None:
    await store.create("/memories/a.md", "pip pip")
    with pytest.raises(MemoryToolError, match="出现 2 次"):
        await store.str_replace("/memories/a.md", "pip", "uv")


async def test_str_replace_missing_text(store: MemoryStore) -> None:
    await store.create("/memories/a.md", "hello")
    with pytest.raises(MemoryToolError, match="找不到"):
        await store.str_replace("/memories/a.md", "world", "x")


async def test_str_replace_missing_file(store: MemoryStore) -> None:
    with pytest.raises(MemoryNotFound):
        await store.str_replace("/memories/nope.md", "a", "b")


# ---------- insert ----------


async def test_insert_at_start_middle_end(store: MemoryStore) -> None:
    await store.create("/memories/a.md", "l1\nl2")

    await store.insert("/memories/a.md", 0, "l0")
    assert (await store.view("/memories/a.md")).count("l0") == 1

    await store.insert("/memories/a.md", 3, "l3")
    out = await store.view("/memories/a.md")
    lines = [line.split("\t", 1)[1] for line in out.split("\n")[1:]]
    assert lines == ["l0", "l1", "l2", "l3"]


async def test_insert_multiline(store: MemoryStore) -> None:
    await store.create("/memories/a.md", "l1")
    await store.insert("/memories/a.md", 1, "x\ny")
    out = await store.view("/memories/a.md")
    assert [line.split("\t", 1)[1] for line in out.split("\n")[1:]] == ["l1", "x", "y"]


@pytest.mark.parametrize("line", [-1, 99])
async def test_insert_out_of_range(store: MemoryStore, line: int) -> None:
    await store.create("/memories/a.md", "l1\nl2")
    with pytest.raises(MemoryToolError, match="越界"):
        await store.insert("/memories/a.md", line, "x")


# ---------- delete ----------


async def test_delete_file_records_version(
    store: MemoryStore, session: AsyncSession
) -> None:
    await store.create("/memories/a.md", "content")
    await store.delete("/memories/a.md")

    with pytest.raises(MemoryNotFound):
        await store.view("/memories/a.md")

    all_versions = await versions(session)
    assert [v.operation for v in all_versions] == ["created", "deleted"]
    # 删除后仍能从版本记录里取回内容
    assert all_versions[-1].content == "content"


async def test_delete_directory_recursive(
    store: MemoryStore, session: AsyncSession
) -> None:
    await store.create("/memories/timeline/2026-07.md", "a")
    await store.create("/memories/timeline/2026-08.md", "b")
    await store.create("/memories/profile/keep.md", "keep")

    result = await store.delete("/memories/timeline")
    assert "共 2 条" in result

    remaining = [m.path for m in (await session.execute(select(Memory))).scalars()]
    assert remaining == ["/memories/profile/keep.md"]


async def test_delete_root_rejected(store: MemoryStore) -> None:
    with pytest.raises(InvalidMemoryPath):
        await store.delete("/memories")


async def test_delete_missing(store: MemoryStore) -> None:
    with pytest.raises(MemoryNotFound):
        await store.delete("/memories/nope.md")


# ---------- rename ----------


async def test_rename_file(store: MemoryStore) -> None:
    await store.create("/memories/a.md", "content")
    await store.rename("/memories/a.md", "/memories/archive/b.md")

    with pytest.raises(MemoryNotFound):
        await store.view("/memories/a.md")
    assert "content" in await store.view("/memories/archive/b.md")


async def test_rename_directory(store: MemoryStore, session: AsyncSession) -> None:
    await store.create("/memories/old/x.md", "x")
    await store.create("/memories/old/sub/y.md", "y")

    await store.rename("/memories/old", "/memories/new")

    paths = sorted(m.path for m in (await session.execute(select(Memory))).scalars())
    assert paths == ["/memories/new/sub/y.md", "/memories/new/x.md"]


async def test_rename_onto_existing_rejected(store: MemoryStore) -> None:
    await store.create("/memories/a.md", "a")
    await store.create("/memories/b.md", "b")
    with pytest.raises(MemoryToolError, match="已存在"):
        await store.rename("/memories/a.md", "/memories/b.md")


async def test_rename_into_own_subdir_rejected(store: MemoryStore) -> None:
    await store.create("/memories/dir/x.md", "x")
    with pytest.raises(InvalidMemoryPath, match="子目录"):
        await store.rename("/memories/dir", "/memories/dir/inner")


async def test_rename_same_path_rejected(store: MemoryStore) -> None:
    await store.create("/memories/a.md", "a")
    with pytest.raises(MemoryToolError, match="相同"):
        await store.rename("/memories/a.md", "/memories/a.md")


async def test_rename_missing(store: MemoryStore) -> None:
    with pytest.raises(MemoryNotFound):
        await store.rename("/memories/nope.md", "/memories/other.md")


# ---------- 路径逃逸必须在每个命令上都被拦住 ----------


@pytest.mark.parametrize(
    "evil", ["/memories/../etc/passwd", "/etc/passwd", "../../secrets.md"]
)
async def test_all_commands_reject_escapes(store: MemoryStore, evil: str) -> None:
    with pytest.raises(InvalidMemoryPath):
        await store.view(evil)
    with pytest.raises(InvalidMemoryPath):
        await store.create(evil, "x")
    with pytest.raises(InvalidMemoryPath):
        await store.str_replace(evil, "a", "b")
    with pytest.raises(InvalidMemoryPath):
        await store.insert(evil, 0, "x")
    with pytest.raises(InvalidMemoryPath):
        await store.delete(evil)
    with pytest.raises(InvalidMemoryPath):
        await store.rename(evil, "/memories/ok.md")
    with pytest.raises(InvalidMemoryPath):
        await store.rename("/memories/ok.md", evil)


# ---------- tree ----------


async def test_tree_infers_directories(store: MemoryStore) -> None:
    await store.create("/memories/MEMORY.md", "index")
    await store.create("/memories/profile/identity.md", "me")
    await store.create("/memories/projects/deep/nested.md", "p")

    paths = [(n.path, n.is_dir) for n in await store.tree()]
    assert ("/memories/MEMORY.md", False) in paths
    assert ("/memories/profile", True) in paths
    assert ("/memories/projects", True) in paths
    assert ("/memories/projects/deep", True) in paths
    assert ("/memories/projects/deep/nested.md", False) in paths
