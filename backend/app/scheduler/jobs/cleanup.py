from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.repositories.conversation_repo import ConversationRepository

logger = get_logger("app.scheduler.cleanup")

RETENTION_DAYS = 30


async def conversation_cleanup(
    session_factory=SessionFactory, retention_days: int = RETENTION_DAYS
) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    async with session_factory() as session:
        repo = ConversationRepository(session)
        purged = await repo.purge_deleted_before(cutoff)
        await session.commit()
    stats = {"cutoff": cutoff.isoformat(), "purged_conversations": purged}
    logger.info("conversation_cleanup done", extra={"kind": "system", **stats})
    return stats