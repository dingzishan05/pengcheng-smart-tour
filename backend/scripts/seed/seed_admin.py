from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.core.security import hash_password
from app.models import User

logger = get_logger("seed.admin")


async def seed_admin() -> None:
    if not settings.admin_initial_password:
        logger.warning("ADMIN_INITIAL_PASSWORD not set, skip admin seed", extra={"kind": "system"})
        return
    async with SessionFactory() as session:
        existing = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalar_one_or_none()
        if existing is not None:
            logger.info("admin exists, skip", extra={"kind": "system"})
            return
        session.add(
            User(
                username="admin",
                password_hash=hash_password(settings.admin_initial_password),
                display_name="系统管理员",
                role="admin",
                is_active=True,
                must_change_password=True,
            )
        )
        await session.commit()
        logger.info("admin created", extra={"kind": "system"})


if __name__ == "__main__":
    asyncio.run(seed_admin())