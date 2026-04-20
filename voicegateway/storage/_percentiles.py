"""Linear-interpolation percentile helper.

SQLite has no native ``PERCENTILE_CONT``; we pull the raw samples and
compute percentiles in Python. The interpolation matches
``numpy.percentile(method='linear')`` so dashboards line up with what
operators expect from Prometheus/Grafana.
"""

from __future__ import annotations

import math


def _percentile_key(p: float) -> str:
    """Stable dict key for a percentile.

    Integer percentiles map to ``p<int>`` (``99 -> "p99"``). Fractional
    percentiles preserve the decimal with ``_`` as separator
    (``99.9 -> "p99_9"``). ``99.0`` and ``99`` both normalize to
    ``"p99"`` so trailing zeros don't surprise callers.
    """
    if p == int(p):
        return f"p{int(p)}"
    return f"p{p}".replace(".", "_")


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
    and produce distinct keys (``99.9 -> "p99_9"``); two inputs that
    map to the same key raise ``ValueError`` rather than silently
    overwriting.
    """
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
