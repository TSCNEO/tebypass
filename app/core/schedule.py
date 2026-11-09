"""Optional community-only time windows, interpreted by the pinned upstream library."""

from stopliga.models import Config
from stopliga.schedule import ActiveSchedule

from app.core.config import Settings, settings


def community_schedule(cfg: Settings | None = None) -> ActiveSchedule:
    cfg = cfg or settings
    return ActiveSchedule(
        Config(
            active_days=cfg.active_days or None,
            active_hours=cfg.active_hours or None,
            active_timezone=cfg.active_timezone,
        )
    )
