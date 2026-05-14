"""Cross-domain helpers used by multiple :mod:`server.api` modules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException


def parse_iso_date(value: str, *, end_of_day: bool) -> float:
    """Parse a YYYY-MM-DD string into a UTC POSIX timestamp."""
    try:
        d = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"invalid date {value!r}: expected YYYY-MM-DD",
        ) from e
    if end_of_day:
        d += timedelta(days=1)
    return d.timestamp()


__all__ = ["parse_iso_date"]
