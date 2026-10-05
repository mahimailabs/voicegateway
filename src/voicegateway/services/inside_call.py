"""Small, allowlisted call summaries for visitor-facing interfaces.

Feed one instance records from exactly one authorized call. No transcript,
metadata, internal IDs, or credentials are included in its output.
"""

from __future__ import annotations

import math
from typing import Any


class InsideCall:
    """Deduplicate metric records and expose cumulative provider measurements."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self.revision = 0

    def record(self, record: Any) -> None:
        if record.modality not in {"stt", "llm", "tts"}:
            return

        def number(value: Any) -> float:
            try:
                result = float(value)
            except (TypeError, ValueError):
                return 0.0
            return max(0.0, result) if math.isfinite(result) else 0.0

        latency = getattr(record, "ttfb_ms", None)
        try:
            latency = float(latency) if latency is not None else None
            if latency is not None and (not math.isfinite(latency) or latency < 0):
                latency = None
        except (TypeError, ValueError):
            latency = None
        self._records[record.id] = {
            "provider": str(record.provider)[:80],
            "model": str(record.model_id)[:120],
            "modality": record.modality,
            "input_units": number(record.input_units),
            "output_units": number(record.output_units),
            "cost_microusd": math.ceil(number(record.cost_usd) * 1_000_000),
            "ttfb_ms": number(latency) if latency is not None else None,
        }
        self.revision += 1

    def snapshot(self) -> dict[str, Any]:
        groups: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in self._records.values():
            key = (row["modality"], row["provider"], row["model"])
            if key not in groups:
                groups[key] = {
                    **row,
                    "input_units": 0.0,
                    "output_units": 0.0,
                    "cost_microusd": 0,
                    "ttfb_ms": None,
                    "samples": [],
                }
            group = groups[key]
            for field in ("input_units", "output_units", "cost_microusd"):
                group[field] += row[field]
            if row["ttfb_ms"] is not None:
                group["samples"].append(row["ttfb_ms"])
        for group in groups.values():
            samples = group.pop("samples")
            group["ttfb_ms"] = round(sum(samples) / len(samples)) if samples else None
        return {"revision": self.revision, "services": list(groups.values())}
