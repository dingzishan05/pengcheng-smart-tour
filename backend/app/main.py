from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.admin import router as admin_router
from app.api.v1.chat import router as chat_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.data import router as data_router
from app.api.v1.health import router as health_router
from app.api.v1.plans import router as plans_router
from app.cache.redis_client import close_redis
from app.core.config import settings
from app.core.db import engine
from app.core.errors import install_exception_handlers
from app.core.logging import TraceMiddleware, get_logger, setup_logging
from app.scheduler.runtime import shutdown_scheduler, start_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level)
    logger = get_logger("app.startup")
    missing = settings.check_required_secrets()
    if missing:
        if settings.app_env == "production":
            raise RuntimeError(f"missing required secrets: {missing}")
        logger.warning("missing secrets (non-production, continue)", extra={"kind": "system", "missing": missing})
    await start_scheduler()
    yield
    await shutdown_scheduler()
    await engine.dispose()
    await close_redis()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(TraceMiddleware)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
install_exception_handlers(app)
app.include_router(health_router)
app.include_router(chat_router, prefix=settings.api_v1_prefix)
app.include_router(plans_router, prefix=settings.api_v1_prefix)
app.include_router(data_router, prefix=settings.api_v1_prefix)
app.include_router(conversations_router, prefix=settings.api_v1_prefix)
app.include_router(admin_router, prefix=settings.api_v1_prefix)