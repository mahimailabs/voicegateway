"""Linear-interpolation percentile helper."""

from __future__ import annotations

import math


def _percentile_key(p: float) -> str:
    """Stable dict key for a percentile."""
    if p == int(p):
        return f"p{int(p)}"
    return f"p{p}".replace(".", "_")


def compute_percentiles(
    values: list[float], percentiles: list[float]
) -> dict[str, float | None]:
    """Return ``{p<int>: value}`` for each percentile."""
    sorted_values = sorted(values) if values else []
    n = len(sorted_values)
    out: dict[str, float | None] = {}

    for p in percentiles:
        key = _percentile_key(p)
        if key in out:
            raise ValueError(
                f"Duplicate percentile key '{key}' for p={p}; inputs collide."
            )
        if n == 0:
            out[key] = None
            continue
        if n == 1:
            out[key] = sorted_values[0]
            continue
        if p <= 0:
            out[key] = sorted_values[0]
            continue
        if p >= 100:
            out[key] = sorted_values[-1]
            continue

        k = (p / 100.0) * (n - 1)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            out[key] = sorted_values[int(f)]
        else:
            out[key] = sorted_values[int(f)] * (c - k) + sorted_values[int(c)] * (k - f)

    return out


def quantile_label(percentile: float) -> str:
    """Return a Prometheus ``quantile`` label for ``percentile``."""
    return f"{percentile / 100:g}"
