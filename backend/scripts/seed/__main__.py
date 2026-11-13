from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.seed import seed_admin, seed_pois


async def main() -> None:
    await seed_admin.seed_admin()
    await seed_pois.run()
    try:
        from scripts.seed import seed_knowledge

        stats = await seed_knowledge.seed()
        print(f"seed_knowledge: {stats}")
    except ImportError:
        print("seed_knowledge not available yet, skipped")
    try:
        from scripts.seed import seed_routes

        await seed_routes.run()
    except ImportError:
        print("seed_routes not available yet, skipped")


if __name__ == "__main__":
    asyncio.run(main())