"""Helpers for ``voicegateway.cli.export_costs_cli``."""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Any

_EXPORT_COLUMNS = (
    "timestamp",
    "project",
    "modality",
    "provider",
    "model",
    "input_units",
    "output_units",
    "calculated_cost_usd",
    "pricing_source",
    "status",
)


_EXPORT_KEY_MAP = {
    "model": "model_id",
    "calculated_cost_usd": "cost_usd",
}


def _format_export_value(column: str, value: Any) -> Any:
    """Format one cell of an export row."""
    if value is None:
        return ""
    if column == "timestamp":
        try:
            return _dt.datetime.fromtimestamp(float(value), tz=_dt.UTC).isoformat()
        except (TypeError, ValueError, OSError):
            return value
    if column == "calculated_cost_usd":
        try:
            return format(Decimal(str(float(value))), "f")
        except (TypeError, ValueError):
            return value
    return value


def _format_export_row(record: dict[str, Any]) -> dict[str, Any]:
    """Project a storage row into the design-spec export schema."""
    out: dict[str, Any] = {}
    for col in _EXPORT_COLUMNS:
        src = _EXPORT_KEY_MAP.get(col, col)
        out[col] = _format_export_value(col, record.get(src))
    return out
