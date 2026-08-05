import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.router import router as chat_router
from app.config import get_settings
from app.db.session import get_session
from app.jobs.router import router as jobs_router
from app.jobs.scheduler import run_daily_consolidation
from app.memory.router import router as memory_router
from app.logging_setup import setup_logging
from app.security import require_api_key


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    task = (
        asyncio.create_task(run_daily_consolidation())
        if get_settings().consolidate_auto
        else None
    )
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


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
    app.include_router(memory_router)
    app.include_router(jobs_router)
    return app


app = create_app()
