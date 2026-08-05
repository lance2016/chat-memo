from collections.abc import AsyncIterator

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
