"""
Background task runner — fire-and-forget async tasks.

Provides spawn() which runs a coroutine in the background without blocking
the HTTP response. Errors are logged but never surfaced to the caller.

This is intentionally simple for the MVP. For production, replace with
a proper task queue (Celery + Redis, or ARQ, or Temporal).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Coroutine, Any

logger = logging.getLogger(__name__)


def spawn(coro: Coroutine, context: str = "") -> asyncio.Task:
    """Schedule a coroutine as a background asyncio task.

    The task is attached to the running event loop. Any exceptions are
    caught and logged at ERROR level so they don't silently disappear.

    Args:
        coro:     The awaitable coroutine to run.
        context:  A human-readable label for logging (e.g. 'process meeting=abc').

    Returns:
        The asyncio.Task (already scheduled).
    """
    async def _wrapper():
        try:
            await coro
        except Exception as e:
            label = f" [{context}]" if context else ""
            logger.error("[background%s] Uncaught exception: %s", label, e, exc_info=True)

    task = asyncio.create_task(_wrapper())
    # Keep a reference so the GC doesn't collect it before it finishes
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)
    return task


# Set to hold references to background tasks to prevent GC collection
_active_tasks: set[asyncio.Task] = set()
