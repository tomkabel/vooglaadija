"""Shared worker state for graceful shutdown coordination.

Holds shutdown_event used by both worker/main.py and worker/processor.py,
breaking the circular import that existed when processor imported from main.

GRACE_PERIOD_SECONDS, shutdown_requested_at, _signal_handler, and
get_grace_period_remaining live in worker/main.py so that
importlib.reload(worker.main) picks up fresh env values and tests
that mutate worker.main.GRACE_PERIOD_SECONDS work correctly.

The shutdown event is created lazily via get_shutdown_event() to avoid
binding to a specific event loop at import time (pytest-asyncio creates
per-test loops and an import-time Event would be bound to a potentially
closed loop).
"""

import asyncio

_shutdown_event: asyncio.Event | None = None


def get_shutdown_event() -> asyncio.Event:
    """Get or create the shared shutdown event.

    Created lazily on first call so it binds to the caller's event loop,
    avoiding the "different loop" error in test environments.
    """
    global _shutdown_event
    if _shutdown_event is None:
        _shutdown_event = asyncio.Event()
    return _shutdown_event


class _ShutdownEventProxy:
    """Lazy proxy for the shared shutdown event.

    Exporting this keeps the long-standing `shutdown_event` import contract while
    preserving lazy event creation for pytest's per-test event loops.
    """

    def set(self) -> None:
        get_shutdown_event().set()

    def clear(self) -> None:
        get_shutdown_event().clear()

    def is_set(self) -> bool:
        return get_shutdown_event().is_set()

    async def wait(self) -> bool:
        return await get_shutdown_event().wait()


shutdown_event = _ShutdownEventProxy()
