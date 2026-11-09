"""Process-wide controller shared by HTTP and lifecycle entry points."""

from app.core.engine import ReconciliationEngine
from app.drivers.base import SyncResult

engine_instance: ReconciliationEngine | None = None


def get_engine() -> ReconciliationEngine:
    global engine_instance
    if engine_instance is None:
        engine_instance = ReconciliationEngine()
    return engine_instance


async def trigger_reconciliation(force: bool = True) -> SyncResult:
    return await get_engine().reconcile(force=force)
