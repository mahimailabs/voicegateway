"""Best-effort worker memory sampling for the fleet heartbeat.

Captures the worker process's RSS and the effective memory ceiling (the
cgroup limit when the process is capped, otherwise total system memory), so
the roster can show how full a worker is. All reads are best-effort: any
failure yields ``None`` so a heartbeat is never broken by a memory read.
"""

from __future__ import annotations

import psutil

_CGROUP_V2_MAX = "/sys/fs/cgroup/memory.max"
_CGROUP_V1_MAX = "/sys/fs/cgroup/memory/memory.limit_in_bytes"


def _read_cgroup_limit(
    v2_path: str = _CGROUP_V2_MAX,
    v1_path: str = _CGROUP_V1_MAX,
) -> int | None:
    """Container memory limit in bytes, or ``None`` if unlimited/unavailable.

    cgroup v2 ``memory.max`` holds an integer or the literal ``max``. cgroup
    v1 ``memory.limit_in_bytes`` uses a huge sentinel for "unlimited". A limit
    at or above total system memory is treated as no real cap.
    """
    for path in (v2_path, v1_path):
        try:
            with open(path) as f:
                raw = f.read().strip()
        except OSError:
            continue
        if not raw or raw == "max":
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if value <= 0 or value >= psutil.virtual_memory().total:
            continue
        return value
    return None


def sample_memory() -> tuple[int | None, int | None]:
    """Return ``(rss_bytes, total_bytes)`` for this worker process.

    ``total`` is the cgroup limit when capped, else total system memory.
    Best-effort: any failure returns ``(None, None)``.
    """
    try:
        rss = psutil.Process().memory_info().rss
        total = _read_cgroup_limit() or psutil.virtual_memory().total
        return int(rss), int(total)
    except Exception:
        return None, None


__all__ = ["sample_memory"]
