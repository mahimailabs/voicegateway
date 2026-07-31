"""Prometheus-format metrics endpoint: GET /v1/metrics.

This endpoint EXPOSES VoiceGateway's own aggregate numbers so an operator's
Prometheus can scrape *this* process. It is the opposite direction from the node
scrape (``services.node_scrape_worker`` -> ``node_samples``), which pulls
exposition text FROM livekit-server / node_exporter INTO this database. The two
are never mixed: nothing scraped from another process is served back out here,
because relabelling livekit-server's counters as ``voicegw_*`` would attribute
another process's numbers to this one.

**No fabricated zeros.** Every series below is omitted when the value is not
known. In Prometheus a ``0`` is an observation: it is graphed, alerted on and
believed. An absent series says "nothing was measured"; a zero says "measured,
and it was none", and only one of those is true when no diagnostics run exists.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

# The gate vocabulary is imported, never restated, so this exposition cannot
# drift into a second severity ladder. The verdict itself is NOT recomputed
# here: livekit_diag.gates decides it once, the run stores it, and this endpoint
# only reads back what was stored.
from voicegateway.livekit_diag.gates import FAIL, PASS, UNKNOWN, WARN
from voicegateway.server.api._deps import get_gateway
from voicegateway.utils.percentiles import quantile_label

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway
    from voicegateway.services.storage_service import StorageService

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metrics", tags=["metrics"])

# Statuses this exposition knows how to encode. Anything else is left out
# rather than mapped: rounding an unrecognised status down to PASS invents a
# healthy reading, and up to FAIL invents an alert nobody measured.
_GATE_STATUSES = frozenset({PASS, WARN, UNKNOWN, FAIL})

# How far back to look for a run that actually gated something. A run in flight
# (queued/running) has no gates yet, and a run that failed before any check
# completed never gets any; without a small lookback the gate series would
# vanish for the duration of every run. Bounded because each row carries its
# full probe payload and this endpoint is scraped on a timer.
_DIAG_RUN_LOOKBACK = 5


@router.get("", response_class=PlainTextResponse)
async def prometheus_metrics(
    request: Request,
    gateway: Gateway = Depends(get_gateway),
) -> str:
    """Prometheus-format metrics."""
    started_at = getattr(request.app.state, "started_at", time.time())
    lines = [
        "# HELP voicegw_uptime_seconds Process uptime",
        "# TYPE voicegw_uptime_seconds gauge",
        f"voicegw_uptime_seconds {time.time() - started_at:.1f}",
        "# HELP voicegw_providers_configured Configured providers",
        "# TYPE voicegw_providers_configured gauge",
        f"voicegw_providers_configured {len(gateway.config.providers)}",
        "# HELP voicegw_projects_configured Configured projects",
        "# TYPE voicegw_projects_configured gauge",
        f"voicegw_projects_configured {len(gateway.config.projects)}",
    ]

    if gateway.storage is not None:
        today = await gateway.storage.get_cost_summary("today")
        lines += [
            "# HELP voicegw_cost_usd_total Total cost in USD (today)",
            "# TYPE voicegw_cost_usd_total counter",
            f'voicegw_cost_usd_total{{period="today"}} {today["total"]:.6f}',
            "# HELP voicegw_requests_total Total requests (today)",
            "# TYPE voicegw_requests_total counter",
        ]
        for provider, data in today.get("by_provider", {}).items():
            lines.append(
                f'voicegw_requests_total{{provider="{provider}"}} {data["requests"]}'
            )
            lines.append(
                f'voicegw_cost_usd_total{{provider="{provider}"}} {data["cost"]:.6f}'
            )

        by_project = await gateway.storage.get_cost_by_project("today")
        for pid, data in by_project.items():
            lines.append(
                f'voicegw_cost_usd_total{{project="{pid}"}} {data["cost"]:.6f}'
            )

        pcts = gateway.config.latency.get("percentiles") or [50.0, 95.0, 99.0]
        latency = await gateway.storage.get_latency_stats("today", percentiles=pcts)
        if latency:
            lines += [
                "# HELP voicegw_request_ttfb_seconds "
                "Per-model time to first byte (seconds, summary)",
                "# TYPE voicegw_request_ttfb_seconds summary",
                "# HELP voicegw_request_total_latency_seconds "
                "Per-model total latency (seconds, summary)",
                "# TYPE voicegw_request_total_latency_seconds summary",
            ]
            for model, s in latency.items():
                for p in pcts:
                    key = f"p{int(p)}"
                    q = quantile_label(p)
                    ttfb_v = s.get("ttfb_percentiles", {}).get(key)
                    if ttfb_v is not None:
                        lines.append(
                            f"voicegw_request_ttfb_seconds"
                            f'{{model="{model}",quantile="{q}"}} '
                            f"{ttfb_v / 1000:.6f}"
                        )
                    lat_v = s.get("latency_percentiles", {}).get(key)
                    if lat_v is not None:
                        lines.append(
                            f"voicegw_request_total_latency_seconds"
                            f'{{model="{model}",quantile="{q}"}} '
                            f"{lat_v / 1000:.6f}"
                        )

        lines += await _diagnostics_lines(gateway.storage)

    return "\n".join(lines) + "\n"


def _label(value: str) -> str:
    """Escape a label value for the text exposition format.

    The gate ids are an internal, bounded vocabulary, but they arrive here out
    of a JSON column: an unescaped quote or newline read back from the database
    would corrupt the whole scrape, not just one line.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _epoch_seconds(raw: object) -> float | None:
    """``raw`` (an ISO-8601 string) as unix seconds, or None if unusable.

    A naive timestamp is rejected rather than assumed to be UTC or local: the
    two readings are hours apart, and "this run finished 5 hours ago" is exactly
    the claim this series exists to make.
    """
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return None if parsed.tzinfo is None else parsed.timestamp()


async def _diagnostics_lines(storage: StorageService) -> list[str]:
    """Aggregate gate series for the newest stored run that gated something.

    Returns ``[]`` -- no series at all -- when no such run exists, so a
    deployment that has never run diagnostics publishes nothing here rather than
    a green-looking zero.

    Aggregate only, by design: gates are counted per gate id and status. The
    per-gate ``subject`` (the probed agent) is deliberately NOT a label, because
    it grows with the number of agents in rooms, and per-call/per-room labels are
    how a metrics endpoint becomes a cardinality incident.
    """
    try:
        runs = await storage.list_diagnostics_runs(limit=_DIAG_RUN_LOOKBACK)
    except Exception:
        # A scrape must not 500 because the diagnostics table is unreadable (an
        # older database, a migration not yet applied). No read means no
        # observation, and an absent series is the honest way to say so.
        _logger.debug("could not read diagnostics runs for /v1/metrics", exc_info=True)
        return []

    for run in runs:
        results = run.get("results")
        gates = results.get("gates") if isinstance(results, dict) else None
        if isinstance(gates, list) and gates:
            # The newest run that gated anything wins; older runs are not
            # consulted, so a stale PASS never outranks a fresh verdict.
            return _render_diagnostics(run, gates)
    return []


def _render_diagnostics(run: dict[str, Any], gates: list[Any]) -> list[str]:
    """The exposition block for one stored run's gates."""
    counts: dict[tuple[str, str], int] = {}
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        gate_id = gate.get("gate")
        status = gate.get("status")
        if not isinstance(gate_id, str) or status not in _GATE_STATUSES:
            continue
        counts[(gate_id, status)] = counts.get((gate_id, status), 0) + 1
    if not counts:
        return []

    lines = [
        "# HELP voicegw_diag_gate_status Health gates in the newest stored "
        "LiveKit diagnostics run, counted by gate id and status. "
        "PASS/WARN/UNKNOWN/FAIL; UNKNOWN is a gate that could NOT be evaluated "
        "and is not a pass (only PASS is). A gate/status pair with no gates is "
        "absent, never 0.",
        "# TYPE voicegw_diag_gate_status gauge",
    ]
    lines += [
        f'voicegw_diag_gate_status{{gate="{_label(gate_id)}",status="{status}"}} {n}'
        for (gate_id, status), n in sorted(counts.items())
    ]

    verdict = run.get("verdict")
    if verdict in _GATE_STATUSES:
        lines += [
            "# HELP voicegw_diag_run_verdict The verdict that run recorded: the "
            "worst of its gates, as decided once by livekit_diag.gates and read "
            "back unchanged here. Only PASS exits 0.",
            "# TYPE voicegw_diag_run_verdict gauge",
            f'voicegw_diag_run_verdict{{verdict="{verdict}"}} 1',
        ]

    ended_at = _epoch_seconds(run.get("ended_at"))
    if ended_at is not None:
        lines += [
            "# HELP voicegw_diag_run_timestamp_seconds Unix time the run behind "
            "the gate series above finished. Those series carry no age of their "
            "own, and a clean verdict from three weeks ago reads identically to "
            "one from a minute ago without this.",
            "# TYPE voicegw_diag_run_timestamp_seconds gauge",
            f"voicegw_diag_run_timestamp_seconds {ended_at:.3f}",
        ]
    return lines
