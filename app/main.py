import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.asr.router import router as asr_router
from app.chat.router import router as chat_router
from app.config import get_settings
from app.db.session import get_session, get_sessionmaker
from app.debug.router import router as debug_router
from app.eval.router import router as eval_router
from app.jobs.router import router as jobs_router
from app.jobs.scheduler import (
    run_backup_ticker,
    run_daily_consolidation,
    run_notification_ticker,
)
from app.llm.catalog import resolve_model_target
from app.llm.router import router as model_router
from app.llm.target import ModelTarget
from app.logging_setup import setup_logging
from app.memory.router import router as memory_router
from app.notify.router import router as notify_router
from app.obs.middleware import ObservabilityMiddleware
from app.obs.router import router as obs_router
from app.obs.tracing import apply_tracing
from app.review.router import router as review_router
from app.security import require_api_key
from app.settings_store import resolve_settings
from app.timeline.router import router as timeline_router
from app.tool_catalog import router as tool_catalog_router
from app.tts.client import warmup
from app.tts.router import public as tts_public_router
from app.tts.router import router as tts_router
from app.tts.tickets import tickets

logger = logging.getLogger(__name__)


async def _sync_tracing() -> None:
    """按生效配置（数据库覆盖叠加在 .env 上）开关 tracing。

    放在 lifespan 而不是 `create_app`：`create_app` 里拿不到数据库，
    只看 .env 的话，设置页里关掉的 tracing 会在每次重启后自己回来。
    """
    async with get_sessionmaker()() as session:
        apply_tracing(await resolve_settings(session))


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
    await _sync_tracing()
    # JOBS_ENABLED=0 时一个 ticker 都不建。开发期开着热重载用：每次改代码都会
    # 重启进程、把 sleep 从头算起，600s 的整理 tick 基本永远等不到 —— 与其让它
    # 半死不活地空转，不如明确关掉，要测就用 POST /api/jobs/* 和 /api/notify/sweep。
    # 注意这**不是** worker 拆分开关的实装，只是把那个开关先备好；同时开两个进程
    # 会让同一天整理两次，见 Settings.jobs_enabled 的注释。
    if not settings.jobs_enabled:
        logger.warning("JOBS_ENABLED=0：后台任务（整理/通知/备份）全部未启动")
    tasks = [
        asyncio.create_task(factory())
        for enabled, factory in (
            (settings.jobs_enabled and settings.consolidate_auto, run_daily_consolidation),
            # 循环内部每分钟重新读一次设置，所以这里只看 jobs_enabled —— 在设置页
            # 打开通知应该下一分钟就生效，而不是要重启进程。空转的代价只是一个 sleep。
            (settings.jobs_enabled, run_notification_ticker),
            # 同上：开关在循环内部每轮重读，在设置页关掉下一轮就生效。
            (settings.jobs_enabled, run_backup_ticker),
            # 预热不受 jobs_enabled 管：它打的是宿主机的 mlx 服务，一次性、不烧钱，
            # 而开发期最不想遇到的就是第一次点朗读干等十几秒。
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
    setup_logging(
        settings.log_level,
        settings.log_color,
        # ObservabilityMiddleware owns access logging so it can keep the
        # request trace ID and duration on the same record. Avoid duplicate
        # uvicorn access lines.
        False,
        settings.log_format,
    )
    app = FastAPI(title="Personal AI Assistant", lifespan=lifespan)

    # Pure ASGI keeps the HTTP span open until a streaming response has sent
    # its final body chunk.
    app.add_middleware(
        ObservabilityMiddleware,
        access=settings.log_access,
        trace_reads=settings.obs_trace_reads,
        trace_http_paths=settings.obs_trace_http_paths,
    )

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
        # 必须走 resolve_settings：闭包里那个 settings 是启动时的基础配置快照，
        # 而聊天走的是「数据库覆盖叠加在基础配置之上」的合并值。在设置页把 provider
        # 换掉之后，用快照会一直报旧的 —— 健康检查报错模型比不报还糟。
        active = await resolve_settings(session)
        # 报当前生效的那个模型 —— 之前无论 PROVIDER 是什么都报 Anthropic 的，会误导。
        try:
            target = await resolve_model_target(session, active)
            return {
                "status": "ok",
                "provider": target.service_slug,
                "model": target.model_id,
            }
        except ValueError:
            # 目录配置不完整时仍让数据库健康检查返回可读状态；聊天接口会给出具体原因。
            fallback = ModelTarget.from_settings(active)
            return {
                "status": "ok",
                "provider": fallback.service_slug,
                "model": fallback.model_id,
            }

    @app.get("/api/ping", dependencies=[Depends(require_api_key)])
    async def ping() -> dict[str, bool]:
        return {"authenticated": True}

    app.include_router(chat_router)
    app.include_router(asr_router)
    app.include_router(memory_router)
    app.include_router(review_router)
    app.include_router(timeline_router)
    app.include_router(notify_router)
    app.include_router(tool_catalog_router)
    app.include_router(model_router)
    app.include_router(jobs_router)
    app.include_router(tts_router)
    app.include_router(tts_public_router)
    app.include_router(debug_router)
    app.include_router(eval_router)
    app.include_router(obs_router)
    return app


app = create_app()
