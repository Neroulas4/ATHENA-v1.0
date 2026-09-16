"""Athens wall-clock schedule; run from a frequent UTC cron."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_TARGET = {"DAILY_BRIEF": 10, "POST_MORTEM": 23}


def due_key(kind: str, now: datetime | None = None) -> str | None:
    if kind not in _TARGET: raise ValueError(f"Unsupported scheduled workflow: {kind}")
    utc = now or datetime.now(timezone.utc)
    if utc.tzinfo is None: raise ValueError("Schedule timestamp must be timezone-aware")
    local = utc.astimezone(ZoneInfo("Europe/Athens"))
    if local.hour != _TARGET[kind] or local.minute >= 15: return None
    return f"{kind}:{local.date().isoformat()}"
