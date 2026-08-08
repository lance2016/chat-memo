import os
from collections.abc import AsyncIterator

# tracing 默认开着（见 config.obs_tracing），但测试里不该真去 instrument：
# 会连 Phoenix、会把 span 发到网上（本机有代理时还会超时重试拖慢整轮），
# 而且 instrumentor 是全局状态，跨用例互相污染。必须在任何 Settings 构造之前设。
os.environ.setdefault("OBS_TRACING", "false")

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base
from app.memory.store import MemoryStore


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """内存 SQLite，够跑存储层逻辑；Postgres 专属行为在集成测试里验。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        yield db

    await engine.dispose()


@pytest.fixture
def store(session: AsyncSession) -> MemoryStore:
    return MemoryStore(session, actor="test")
