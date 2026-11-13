from __future__ import annotations

import asyncio

from app.agent.checkpointer import build_checkpointer
from app.agent.graph import build_graph
from app.core.db import SessionFactory

_graph = None
_build_lock = asyncio.Lock()


async def get_graph():
    global _graph
    if _graph is None:
        async with _build_lock:
            if _graph is None:
                checkpointer = await build_checkpointer()
                _graph = build_graph(SessionFactory, checkpointer=checkpointer)
    return _graph


def set_graph(graph) -> None:
    global _graph
    _graph = graph


def reset_graph() -> None:
    global _graph
    _graph = None