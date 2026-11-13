from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select


from app.core.logging import get_logger
from app.rag.ingestion import TOPICS, ingest_document
from scripts.seed.knowledge_data import KNOWLEDGE_DOCUMENTS

logger = get_logger("app.seed.knowledge")


async def seed(dry_run: bool = False, operator_id: int = 1) -> dict:
    stats = {"created": 0, "skipped": 0, "failed": 0, "total": len(KNOWLEDGE_DOCUMENTS)}
    if dry_run:
        stats["created"] = len(KNOWLEDGE_DOCUMENTS)
        return stats

    from sqlalchemy import select

    from app.core.db import SessionFactory
    from app.models import KnowledgeDocument

    async with SessionFactory() as session:
        existing = select(KnowledgeDocument.title, KnowledgeDocument.content)
        rows = (await session.execute(existing)).all()
        existing_map = dict(rows)

        for doc in KNOWLEDGE_DOCUMENTS:
            title = doc["title"]
            if title in existing_map and existing_map[title] == doc["content"]:
                stats["skipped"] += 1
                continue
            if dry_run:
                stats["created"] += 1
                continue
            try:
                await ingest_document(
                    session,
                    title=title,
                    topic=doc["topic"],
                    content=doc["content"],
                    source=doc["source"],
                    operator_id=operator_id,
                )
                stats["created"] += 1
            except Exception as exc:
                stats["failed"] += 1
                logger.error(
                    "seed knowledge failed",
                    extra={"kind": "data", "title": title, "error": str(exc)[:200]},
                )
        await session.commit()
    return stats


def validate_seed_data() -> list[str]:
    problems: list[str] = []
    titles: set[str] = set()
    for doc in KNOWLEDGE_DOCUMENTS:
        title = doc.get("title", "")
        if title in titles:
            problems.append(f"标题重复: {title}")
        titles.add(title)
        if doc.get("topic") not in TOPICS:
            problems.append(f"主题非法: {title}")
        if not doc.get("source"):
            problems.append(f"缺少来源: {title}")
        if len(doc.get("content", "")) < 20:
            problems.append(f"正文过短: {title}")
    by_topic: dict[str, int] = {}
    for doc in KNOWLEDGE_DOCUMENTS:
        by_topic[doc["topic"]] = by_topic.get(doc["topic"], 0) + 1
    ranges = {"两汉文化": (8, 12), "景点介绍": (15, 30), "特色美食": (5, 8), "交通常识": (3, 5)}
    for topic, (low, high) in ranges.items():
        count = by_topic.get(topic, 0)
        if not low <= count <= high:
            problems.append(f"主题 {topic} 篇数 {count} 不在 [{low},{high}]")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed RAG knowledge base")
    parser.add_argument("--dry-run", action="store_true", help="仅校验不入库")
    parser.add_argument("--operator-id", type=int, default=1)
    args = parser.parse_args()

    problems = validate_seed_data()
    if problems:
        for p in problems:
            print(f"[INVALID] {p}")
        sys.exit(1)
    print(f"[OK] seed data validated: {len(KNOWLEDGE_DOCUMENTS)} documents, 4 topics covered")

    stats = asyncio.run(seed(dry_run=args.dry_run, operator_id=args.operator_id))
    print(
        f"[DONE] total={stats['total']} created={stats['created']} "
        f"skipped={stats['skipped']} failed={stats['failed']}"
    )


if __name__ == "__main__":
    main()