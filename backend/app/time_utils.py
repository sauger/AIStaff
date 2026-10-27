from __future__ import annotations

from datetime import UTC, datetime


def as_utc(value: datetime | None) -> datetime | None:
    """Return a timezone-aware UTC datetime for SQLite-read values."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
