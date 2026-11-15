"""Process-wide controller shared by HTTP and lifecycle entry points."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.engine import ReconciliationEngine
from app.drivers.base import SyncResult

logger = logging.getLogger("tebypass.runtime")

engine_instance: ReconciliationEngine | None = None
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
_reconcile_task: asyncio.Task[Any] | None = None
_pending_reconcile_force: bool | None = None
_pending_reconcile_runner: Callable[[], Awaitable[Any]] | None = None


def get_engine() -> ReconciliationEngine:
    global engine_instance
    if engine_instance is None:
        engine_instance = ReconciliationEngine()
    return engine_instance


async def trigger_reconciliation(force: bool = True) -> SyncResult:
    return await get_engine().reconcile(force=force)


def _on_background_task_done(task: asyncio.Task[Any]) -> None:
    _BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except Exception:  # noqa: BLE001 - defensive guard when inspecting task state
        return
    if exc is not None:
        logger.error(
            "Unhandled exception in background task %s",
            task.get_name(),
            exc_info=exc,
        )


def track_background_task(
    coro: Awaitable[Any],
    *,
    name: str | None = None,
) -> asyncio.Task[Any]:
    """Schedule a coroutine as a tracked background task with strong reference and error logging."""

    async def _runner() -> Any:
        return await coro

    task: asyncio.Task[Any] = asyncio.create_task(_runner(), name=name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_on_background_task_done)
    return task


def schedule_reconciliation(
    force: bool = True,
    *,
    runner: Callable[[], Awaitable[Any]] | None = None,
) -> asyncio.Task[Any]:
    """Schedule a tracked background reconciliation, coalescing overlapping triggers."""
    global _reconcile_task, _pending_reconcile_force, _pending_reconcile_runner

    loop = asyncio.get_running_loop()
    if (
        _reconcile_task is not None
        and not _reconcile_task.done()
        and _reconcile_task.get_loop() is loop
    ):
        _pending_reconcile_force = bool(_pending_reconcile_force or force)
        if runner is not None:
            _pending_reconcile_runner = runner
        return _reconcile_task

    _pending_reconcile_force = None
    _pending_reconcile_runner = None

    async def _run_coalesced() -> None:
        global _reconcile_task, _pending_reconcile_force, _pending_reconcile_runner
        current_force: bool | None = force
        current_runner = runner
        try:
            while current_force is not None:
                try:
                    if current_runner is not None:
                        await current_runner()
                    else:
                        await trigger_reconciliation(force=current_force)
                except asyncio.CancelledError:
                    raise
                except Exception as ex:  # noqa: BLE001 - isolate background failure
                    logger.warning("Background reconciliation failed: %s", ex)
                current_force = _pending_reconcile_force
                current_runner = _pending_reconcile_runner
                _pending_reconcile_force = None
                _pending_reconcile_runner = None
        finally:
            if _reconcile_task is asyncio.current_task():
                _reconcile_task = None

    task = track_background_task(_run_coalesced(), name="reconciliation")
    _reconcile_task = task
    return task


async def drain_background_tasks(timeout: float = 5.0) -> None:
    """Wait briefly for tracked background tasks on shutdown, then cancel stragglers."""
    global _reconcile_task, _pending_reconcile_force, _pending_reconcile_runner
    _pending_reconcile_force = None
    _pending_reconcile_runner = None

    loop = asyncio.get_running_loop()
    pending: list[asyncio.Task[Any]] = []
    for task in list(_BACKGROUND_TASKS):
        if task.done() or task.get_loop() is not loop:
            _BACKGROUND_TASKS.discard(task)
            continue
        pending.append(task)

    if not pending:
        if _reconcile_task is not None and (
            _reconcile_task.done() or _reconcile_task.get_loop() is not loop
        ):
            _reconcile_task = None
        return

    _, still_pending = await asyncio.wait(pending, timeout=timeout)
    if still_pending:
        for task in still_pending:
            task.cancel()
        await asyncio.gather(*still_pending, return_exceptions=True)

    _reconcile_task = None
