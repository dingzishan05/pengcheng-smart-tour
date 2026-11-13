from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.scheduler.jobs.cleanup import conversation_cleanup
from app.scheduler.jobs.expiry_patrol import expiry_patrol
from app.scheduler.jobs.route_warmup import route_matrix_warmup
from app.scheduler.jobs.source_health import source_health_check
from app.scheduler.jobs.weather_prefetch import weather_prefetch

logger = get_logger("app.scheduler")

JOB_IDS = (
    "expiry_patrol",
    "source_health_check",
    "weather_prefetch",
    "route_matrix_warmup",
    "conversation_cleanup",
)

JOB_FUNCS = {
    "expiry_patrol": expiry_patrol,
    "source_health_check": source_health_check,
    "weather_prefetch": weather_prefetch,
    "route_matrix_warmup": route_matrix_warmup,
    "conversation_cleanup": conversation_cleanup,
}

_scheduler: AsyncIOScheduler | None = None


def build_scheduler(session_factory=SessionFactory) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        expiry_patrol,
        CronTrigger(hour=3, minute=0),
        id="expiry_patrol",
        kwargs={"session_factory": session_factory},
        replace_existing=True,
    )
    scheduler.add_job(
        source_health_check,
        IntervalTrigger(minutes=30),
        id="source_health_check",
        kwargs={"session_factory": session_factory},
        replace_existing=True,
    )
    scheduler.add_job(
        weather_prefetch,
        CronTrigger(minute=0),
        id="weather_prefetch",
        kwargs={"session_factory": session_factory},
        replace_existing=True,
    )
    scheduler.add_job(
        route_matrix_warmup,
        CronTrigger(hour=2, minute=0),
        id="route_matrix_warmup",
        kwargs={"session_factory": session_factory},
        replace_existing=True,
    )
    scheduler.add_job(
        conversation_cleanup,
        CronTrigger(hour=4, minute=0),
        id="conversation_cleanup",
        kwargs={"session_factory": session_factory},
        replace_existing=True,
    )
    return scheduler


async def start_scheduler(session_factory=SessionFactory) -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return
    _scheduler = build_scheduler(session_factory)
    _scheduler.start()
    logger.info("scheduler started", extra={"kind": "system", "jobs": list(JOB_IDS)})


async def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def scheduler_running() -> bool:
    return _scheduler is not None and _scheduler.running


def scheduler_status() -> dict:
    jobs: list[dict] = []
    if _scheduler is not None:
        for job in _scheduler.get_jobs():
            jobs.append(
                {
                    "id": job.id,
                    "next_run_at": job.next_run_time.isoformat()
                    if job.next_run_time
                    else None,
                }
            )
    return {"running": scheduler_running(), "jobs": jobs}


async def trigger_job(job_id: str, session_factory=SessionFactory) -> dict:
    func = JOB_FUNCS.get(job_id)
    if func is None:
        raise KeyError(f"unknown scheduler job: {job_id}")
    result = await func(session_factory=session_factory)
    return {"job_id": job_id, "result": result}