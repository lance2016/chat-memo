import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.router import router as chat_router
from app.asr.router import router as asr_router
from app.config import get_settings
from app.db.session import get_session, get_sessionmaker
from app.jobs.router import router as jobs_router
from app.jobs.scheduler import run_daily_consolidation
from app.debug.router import router as debug_router
from app.memory.router import router as memory_router
from app.tts.client import warmup
from app.tts.router import public as tts_public_router
from app.tts.router import router as tts_router
from app.tts.tickets import tickets
from app.logging_setup import setup_logging
from app.security import require_api_key
from app.settings_store import resolve_settings


async def _warm_tts() -> None:
    """启动时把语音模型的权重加载进去。失败静默 —— 见 tts.client.warmup。"""
    async with get_sessionmaker()() as session:
        settings = await resolve_settings(session)
    if settings.tts_mode != "off":
        await warmup(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # 工厂而不是协程对象 —— 没启用的那个如果被创建出来又不 await，
    # Python 会在关闭时甩一条 "coroutine was never awaited"。
    tasks = [
        asyncio.create_task(factory())
        for enabled, factory in (
            (settings.consolidate_auto, run_daily_consolidation),
            (settings.tts_warmup, _warm_tts),
        )
        if enabled
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # 关进程时队列里还没播的句子要连带取消，否则后台合成任务会拖住关闭
        tickets.cancel_all()


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_color, settings.log_access)
    app = FastAPI(title="Personal AI Assistant", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
        await session.execute(text("SELECT 1"))
        # 报当前生效的那个模型 —— 之前无论 PROVIDER 是什么都报 Anthropic 的，会误导。
        active_model = (
            settings.model if settings.provider == "anthropic" else settings.deepseek_model
        )
        return {"status": "ok", "provider": settings.provider, "model": active_model}

    @app.get("/api/ping", dependencies=[Depends(require_api_key)])
    async def ping() -> dict[str, bool]:
        return {"authenticated": True}

    app.include_router(chat_router)
    app.include_router(asr_router)
    app.include_router(memory_router)
    app.include_router(jobs_router)
    app.include_router(tts_router)
    app.include_router(tts_public_router)
    app.include_router(debug_router)
    return app


app = create_app()
