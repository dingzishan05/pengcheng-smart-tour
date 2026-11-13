from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.scheduler.jobs.cleanup as cleanup_mod
from tests.api.test_chat import FakeSessionCtx


class FakeCleanupConvRepo:
    conversations: list = []
    purge_cutoffs: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.conversations = []
        cls.purge_cutoffs = []

    async def purge_deleted_before(self, cutoff):
        self.purge_cutoffs.append(cutoff)
        remaining = []
        purged = 0
        for conv in FakeCleanupConvRepo.conversations:
            if conv.deleted_at is not None and conv.deleted_at < cutoff:
                purged += 1
            else:
                remaining.append(conv)
        FakeCleanupConvRepo.conversations = remaining
        return purged


@pytest.fixture()
def cleanup_env(monkeypatch):
    FakeCleanupConvRepo.reset()
    monkeypatch.setattr(cleanup_mod, "ConversationRepository", FakeCleanupConvRepo)
    yield monkeypatch


def session_factory():
    return FakeSessionCtx()


def make_conv(conv_id, deleted_at):
    return SimpleNamespace(id=conv_id, deleted_at=deleted_at)


def test_cleanup_purges_old_deleted_conversations(cleanup_env):
    now = datetime.now(timezone.utc)
    FakeCleanupConvRepo.conversations = [
        make_conv(1, now - timedelta(days=31)),
        make_conv(2, now - timedelta(days=45)),
        make_conv(3, now - timedelta(days=29)),
        make_conv(4, None),
    ]

    stats = asyncio.run(cleanup_mod.conversation_cleanup(session_factory))

    assert stats["purged_conversations"] == 2
    assert [c.id for c in FakeCleanupConvRepo.conversations] == [3, 4]
    assert len(FakeCleanupConvRepo.purge_cutoffs) == 1
    cutoff = FakeCleanupConvRepo.purge_cutoffs[0]
    expected = datetime.now(timezone.utc) - timedelta(days=30)
    assert abs((cutoff - expected).total_seconds()) < 60


def test_cleanup_rerun_is_idempotent(cleanup_env):
    now = datetime.now(timezone.utc)
    FakeCleanupConvRepo.conversations = [make_conv(1, now - timedelta(days=40))]

    first = asyncio.run(cleanup_mod.conversation_cleanup(session_factory))
    second = asyncio.run(cleanup_mod.conversation_cleanup(session_factory))

    assert first["purged_conversations"] == 1
    assert second["purged_conversations"] == 0
    assert FakeCleanupConvRepo.conversations == []


def test_cleanup_custom_retention(cleanup_env):
    now = datetime.now(timezone.utc)
    FakeCleanupConvRepo.conversations = [make_conv(1, now - timedelta(days=10))]

    stats = asyncio.run(
        cleanup_mod.conversation_cleanup(session_factory, retention_days=7)
    )

    assert stats["purged_conversations"] == 1