"""Linear-interpolation percentile helper.

SQLite has no native ``PERCENTILE_CONT``; we pull the raw samples and
compute percentiles in Python. The interpolation matches
``numpy.percentile(method='linear')`` so dashboards line up with what
operators expect from Prometheus/Grafana.
"""

from __future__ import annotations

import math


def compute_percentiles(
    values: list[float], percentiles: list[float]
) -> dict[str, float | None]:
    """Return ``{p<int>: value}`` for each percentile.

    - Empty input → every percentile is ``None``.
    - Single value → every percentile equals that value (degenerate but
      more useful to display than ``None``).
    - Otherwise → linear interpolation between the two nearest ranks.

    ``percentiles`` are in ``[0, 100]``. Values outside that range are
    clamped to the min/max sample. Fractional percentiles are accepted
    (``99.9`` → key ``"p99"``; the decimal is dropped to keep keys
    stable for JSON consumers).
    """
    sorted_values = sorted(values) if values else []
    n = len(sorted_values)
    out: dict[str, float | None] = {}

    for p in percentiles:
        key = f"p{int(p)}"
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
            out[key] = (
                sorted_values[int(f)] * (c - k)
                + sorted_values[int(c)] * (k - f)
            )

    return out


def quantile_label(percentile: float) -> str:
    """Return a Prometheus ``quantile`` label for ``percentile``.

    ``50 -> "0.5"``, ``95 -> "0.95"``, ``99 -> "0.99"``. Matches the
    Prometheus summary convention.
    """
    return f"{percentile / 100:g}"
