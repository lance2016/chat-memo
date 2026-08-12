import os
from collections.abc import AsyncIterator

# tracing 默认开着（见 config.obs_tracing），但测试里不该真去 instrument：
# 会连 Phoenix、会把 span 发到网上（本机有代理时还会超时重试拖慢整轮），
# 而且 instrumentor 是全局状态，跨用例互相污染。必须在任何 Settings 构造之前设。
os.environ.setdefault("OBS_TRACING", "false")

# **测试一律不读开发机的 `.env`。**
# `Settings` 的 model_config 指向仓库根的 .env（见 app/config.py），CI 里没有这个文件、
# 本机有 —— 同一份用例于是在两处解析出不同配置，而且症状离原因很远：加一个
# OPENAI_BASE_URL 到 .env，会让 5 个跟 OpenAI 毫无关系的思考开关用例开始失败
# （`ModelTarget.from_settings` 里有「provider 没被显式设置时 OPENAI_BASE_URL 自动接管」
# 这一支）。本机绿、CI 红或者反过来，是最难查的一类。
#
# 具体做法见文件末尾的 pytest_configure。

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
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


def pytest_configure() -> None:
    """把 `Settings` 和仓库根的 `.env` 断开。

    放在这里而不是模块顶层，是因为顶层插一行 import 会让后面所有 import 触发 E402。
    时机上够早：全代码库没有任何模块在 import 期构造 `Settings`
    （`get_settings` 是 lru_cache 的惰性调用），所以第一个用例跑之前断开就行。
    """
    Settings.model_config["env_file"] = None
    get_settings.cache_clear()
