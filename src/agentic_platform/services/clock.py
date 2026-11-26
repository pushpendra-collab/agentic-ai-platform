"""Clock service: the single source of "now" for the platform (injectable for tests)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class UnknownTimezoneError(ValueError):
    pass


class ClockService:
    def __init__(self, now_utc: Callable[[], datetime] | None = None) -> None:
        self._now_utc = now_utc or (lambda: datetime.now(UTC))

    def now(self, timezone_name: str = "UTC") -> datetime:
        try:
            tz = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise UnknownTimezoneError(f"Unknown IANA timezone: {timezone_name!r}") from exc
        return self._now_utc().astimezone(tz)
