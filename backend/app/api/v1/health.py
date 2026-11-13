from fastapi import APIRouter, Response
from sqlalchemy import text

from app.cache.redis_client import ping_redis
from app.core.db import engine
from app.core.metrics import snapshot as metrics_snapshot
from app.scheduler.runtime import scheduler_status

router = APIRouter(tags=["health"])


async def _check_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@router.get("/health")
async def health(response: Response) -> dict:
    db_ok = await _check_db()
    redis_ok = await ping_redis()
    scheduler_info = scheduler_status()
    all_ok = db_ok and redis_ok and scheduler_info["running"]
    payload = {
        "status": "ok" if all_ok else "degraded",
        "db": db_ok,
        "redis": redis_ok,
        "scheduler": scheduler_info["running"],
        "scheduler_jobs": scheduler_info["jobs"],
        "metrics": metrics_snapshot(),
    }
    if not all_ok:
        response.status_code = 503
    return payload