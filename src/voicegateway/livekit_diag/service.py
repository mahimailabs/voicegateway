"""Bounded execution of the livekit_diag probes from resolved LiveKit creds.

RealProbes wraps LiveKitAdmin/ProbeRunner/SfuProbe exactly as the CLI does, with
hard caps applied. execute_run runs each requested check under try/except so one
failing check never kills the others (mirrors the CLI's best-effort behaviour).
Tests inject a fake `probes` object with the same async method surface.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
from typing import Any

from voicegateway.livekit_diag import gates

PER_CHECK_TIMEOUT_SECONDS = 120.0
MAX_LOAD_CLIENTS = 25
MAX_LOAD_SECONDS = 30.0
MAX_LATENCY_TRIALS = 3
_DEFAULT_RAMP = [2, 10, 25]
_TARGET_RTT_MS = 50.0
_MAX_LOSS = 1.0
# The roster is an optional HTTP enrichment on top of the agents check, so it
# gets a budget of its own well inside PER_CHECK_TIMEOUT_SECONDS: a collector
# that hangs must cost the roster, never the in-room agent list that was already
# read successfully. fetch_roster's own httpx timeout is 10s; this is the
# backstop for a hang it cannot see (DNS, TLS, a proxy holding the socket).
_ROSTER_TIMEOUT_SECONDS = 15.0


def _safe_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def _safe_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def clamp_config(config: dict[str, Any]) -> dict[str, Any]:
    """Clamp client-supplied config to the hard caps; fill defaults."""
    ramp_raw = config.get("ramp")
    if not isinstance(ramp_raw, list):
        ramp_raw = _DEFAULT_RAMP
    ramp: list[int] = []
    for n in ramp_raw:
        try:
            val = int(n)
            if val > 0:
                ramp.append(min(val, MAX_LOAD_CLIENTS))
        except (ValueError, TypeError):
            pass
    ramp = ramp[:6] or _DEFAULT_RAMP
    duration = min(
        max(1.0, _safe_float(config.get("duration", 20.0), 20.0)), MAX_LOAD_SECONDS
    )
    # Cap per-step duration so N steps * duration <= MAX_LOAD_SECONDS total.
    duration = min(duration, MAX_LOAD_SECONDS / max(1, len(ramp)))
    trials = min(max(1, _safe_int(config.get("trials", 2), 2)), MAX_LATENCY_TRIALS)
    return {
        "target_ms": _safe_float(config.get("target_ms", 1500), 1500.0),
        "ramp": ramp,
        "duration": duration,
        "trials": trials,
    }


_diag_cache: Any = None


def _diag() -> Any:
    """Import the engine's livekit_diag probes lazily, cached.

    Kept out of module scope on purpose: the diagnostics router imports this
    module at app startup, so a stale or missing engine pin must not be able to
    crash the whole cloud API. Any import failure here is raised inside
    execute_run's per-check try/except and surfaces as a failed check, not a
    dead process (and a failed /health that blocks every deploy).
    """
    global _diag_cache
    if _diag_cache is None:
        from types import SimpleNamespace

        import voicegateway.livekit_diag as pkg
        from voicegateway.livekit_diag.admin import LiveKitAdmin
        from voicegateway.livekit_diag.client import SyntheticClient, UtteranceSource
        from voicegateway.livekit_diag.latency import (
            ComponentReader,
            ProbeRunner,
            summarize,
        )
        from voicegateway.livekit_diag.report import agents_json
        from voicegateway.livekit_diag.resources import ResourceMonitor
        from voicegateway.livekit_diag.roster import fetch_roster
        from voicegateway.livekit_diag.sfu import SfuProbe, find_knee

        _diag_cache = SimpleNamespace(
            pkg=pkg,
            ComponentReader=ComponentReader,
            LiveKitAdmin=LiveKitAdmin,
            SyntheticClient=SyntheticClient,
            UtteranceSource=UtteranceSource,
            ProbeRunner=ProbeRunner,
            summarize=summarize,
            agents_json=agents_json,
            ResourceMonitor=ResourceMonitor,
            SfuProbe=SfuProbe,
            find_knee=find_knee,
            fetch_roster=fetch_roster,
        )
    return _diag_cache


def _utterance_path() -> str:
    return str(pathlib.Path(_diag().pkg.__file__).parent / "assets" / "probe.wav")


# The read-back polls because the agent writes its STT/LLM/TTS rows from another
# process and may still be flushing when the probe's last turn ends. ~2s total,
# matching the CLI (voicegw livekit latency).
_READBACK_POLL_ATTEMPTS = 6
_READBACK_POLL_DELAY = 0.4


def _component_reader(store: Any) -> Any:
    """A ComponentReader over ``store``, or a storeless one when there is none.

    A storeless reader returns None rather than a fabricated split, which is the
    honest answer for an agent whose telemetry goes to a remote collector: those
    rows were never written here, so this host cannot know the breakdown.
    """
    d = _diag()
    if store is None:
        return d.ComponentReader()
    return d.ComponentReader(
        store, poll_attempts=_READBACK_POLL_ATTEMPTS, poll_delay=_READBACK_POLL_DELAY
    )


def _resource_json(report: Any) -> dict[str, Any] | None:
    """Serialize a ResourceReport, with its "not sampled" zeros turned into None.

    ``ResourceReport`` defaults an unsampled metric to 0.0 (``max(..., default=0.0)``
    over an empty list, a net delta that needs two samples, and psutil's first
    ``cpu_percent(interval=None)`` call which always returns 0.0). A 0 here
    therefore means "not measured", not "idle" - and a fabricated 0% CPU printed
    next to a 25-client ramp reads as a host with infinite headroom, which is the
    opposite of what this block exists to say. ``saturated`` follows the CPU
    sample because it is derived from it: with no sample, saturation is unknown,
    not False.
    """
    if report is None:
        return None
    per_client = dict(report.per_client or {})
    cpu_peak = float(report.cpu_peak) if report.cpu_peak else None
    net_kbps_up = float(report.net_kbps_up) if report.net_kbps_up else None
    return {
        "cpu_peak": cpu_peak,
        "mem_peak_mb": float(report.mem_peak_mb) if report.mem_peak_mb else None,
        "net_kbps_up": net_kbps_up,
        "saturated": bool(report.saturated) if cpu_peak is not None else None,
        "per_client": {
            "cpu_pct": per_client.get("cpu_pct") if cpu_peak is not None else None,
            "kbps_up": per_client.get("kbps_up") if net_kbps_up is not None else None,
        },
        # Already None when the per-client CPU share was not measurable.
        "sustainable_n": report.sustainable_n,
    }


class RealProbes:
    """Runs the engine probes against a live LiveKit server.

    ``store`` is the local VoiceGateway store (a StorageService) used to read the
    STT/LLM/TTS split back out of the instrumented agent's own telemetry rows.
    Without it every probe reports end-to-end time only, and the component split
    is None: the breakdown lives in rows the agent wrote, not in anything the
    probe can time from outside.
    """

    def __init__(self, store: Any = None) -> None:
        self._store = store

    async def agents(self, creds) -> dict[str, Any]:
        """In-room agents from the LiveKit server API, plus the heartbeat roster.

        The two are different populations, and the roster is the one that closes
        the gap: LiveKit's server API only reports a worker once it is IN a room,
        so an idle registered worker is invisible to ``list_agents``. The roster
        comes from the agents' own ``register_worker`` heartbeat via the
        collector, and is a best-effort enrichment: ``roster`` is None when no
        collector is configured (the UI then says how to enable it) and a list
        when it is, so "not configured" never renders as "zero workers".
        """
        d = _diag()
        admin = d.LiveKitAdmin(creds)
        admin.url = creds.url
        try:
            rows = await admin.list_agents()
        finally:
            await admin.aclose()
        return {"agents": d.agents_json(rows), "roster": await self._roster()}

    async def _roster(self) -> list[dict[str, Any]] | None:
        """The collector's worker roster, or None when there is no collector.

        Bounded and non-raising on purpose: this runs inside the agents check,
        whose in-room result is already measured by the time we get here, so a
        slow or broken collector must degrade to "no roster" rather than time the
        whole check out and throw the agent list away with it.
        """
        collector = os.environ.get("VOICEGW_COLLECTOR_URL")
        if not collector:
            return None
        try:
            rows = await asyncio.wait_for(
                _diag().fetch_roster(collector, os.environ.get("VOICEGW_API_KEY")),
                _ROSTER_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 - the roster must never fail the check
            return []
        return list(rows)

    async def sfu(self, creds, load: bool, config: dict[str, Any]) -> dict[str, Any]:
        """SFU baseline, and (when ``load``) the ramp plus the prober's own load.

        ``resource`` is the prober host's CPU/mem/net during the ramp. It is
        reported, not dropped, because without it a knee is unattributable: a
        laptop that pegged its own CPU at 25 clients produces exactly the same
        rtt curve as an SFU that ran out of headroom, and only this block says
        which one happened.

        ``target_rtt_ms`` is the threshold ``knee`` was computed against.
        ``find_knee`` returns None for two opposite outcomes (no tier breached,
        or the FIRST tier breached), so a reader that cannot compare the steps
        against the threshold cannot tell a clean ramp from a total failure.

        ``loss_pct`` is carried through as the SDK reported it, which today is a
        hardcoded 0.0 in ``sfu.py`` (per-connection loss is not exposed). It is
        not a measurement and must not be rendered as one; ``quality`` is the
        coarse signal that is real.

        The baseline and each ramp step carry ``samples`` (how many ping
        round-trips came back) and ``rtt_stat`` beside their ``rtt_ms``, because
        a reading whose pings all timed out reports an rtt of 0.0 and only the
        count says that 0.0 is a placeholder rather than a very fast SFU.
        ``gates.sfu_capacity_gate`` reads the ramp's; ``gates.sfu_quality_gate``
        reads the baseline's.
        """
        d = _diag()
        admin = d.LiveKitAdmin(creds)
        admin.url = creds.url
        probe = d.SfuProbe(
            admin, lambda u, t: d.SyntheticClient(u, t), d.ResourceMonitor()
        )
        steps: list[Any] = []
        resource: Any = None
        try:
            base = await probe.baseline("vg-diag-sfu")
            if load:
                steps, resource = await probe.ramp(
                    "vg-diag-sfu",
                    config["ramp"],
                    config["duration"],
                    _TARGET_RTT_MS,
                    _MAX_LOSS,
                )
            knee = d.find_knee(steps, _TARGET_RTT_MS, _MAX_LOSS) if steps else None
        finally:
            await admin.aclose()
        return {
            "baseline": {
                "rtt_ms": base.rtt_ms,
                "loss_pct": base.loss_pct,
                "quality": base.quality,
                # The same two keys the ramp steps carry, for the same reason
                # and one worse case: ``quality`` and ``rtt_ms`` are INDEPENDENT
                # readings here. ``quality`` is the SDK's own peer-connection
                # metric, ``rtt_ms`` is a mean over ping round trips, so a
                # connection that came up while every ping timed out reports
                # quality "Excellent" beside an rtt of 0.0 over zero samples.
                # Without the count, sfu_quality_gate read only the quality and
                # certified that baseline healthy. int/str, so both survive the
                # round trip through the stored run JSON.
                "samples": base.samples,
                "rtt_stat": base.rtt_stat,
            },
            "ramp": [
                {
                    "clients": s.clients,
                    "rtt_ms": s.rtt_ms,
                    "loss_pct": s.loss_pct,
                    "quality": s.quality,
                    # What rtt_ms IS. A tier whose pings all timed out reports
                    # 0.0, and without a count of the round-trips behind it that
                    # 0.0 is indistinguishable from a very fast SFU: it read as
                    # comfortably inside budget, so a completely unmeasured ramp
                    # passed sfu_capacity. Both keys are ints/strings, so they
                    # survive the round trip through the stored run JSON.
                    "samples": s.samples,
                    "rtt_stat": s.rtt_stat,
                }
                for s in steps
            ],
            "knee": knee,
            "target_rtt_ms": _TARGET_RTT_MS,
            "resource": _resource_json(resource),
        }

    async def latency(self, creds, config: dict[str, Any]) -> dict[str, Any]:
        d = _diag()
        admin = d.LiveKitAdmin(creds)
        admin.url = creds.url
        try:
            targets = [r.agent_name for r in await admin.list_agents()]
            runner = d.ProbeRunner(
                admin,
                lambda u, t: d.SyntheticClient(creds.url, t),
                d.UtteranceSource(_utterance_path()),
                _component_reader(self._store),
            )
            out = []
            for name in targets:
                r = await runner.probe(name, config["trials"], True, None, "")
                out.append(
                    {"agent": name, "stats": d.summarize(r), "components": r.components}
                )
        finally:
            await admin.aclose()
        return {"agents": out}


MAX_PROBE_TRIALS = 3
PROBE_TIMEOUT_SECONDS = 120.0

_ROOM_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def probe_room_name(agent_id: str, nonce: str) -> str:
    """Build the throwaway room name for a single-agent probe.

    The ``vg-probe-`` prefix is not cosmetic: it is what marks the rows this call
    produces as synthetic, so they can be kept out of the agent's real-traffic
    rollups (see request_log_repository.exclude_probes_clause). ``nonce`` makes
    the room unique per press; the caller supplies it so this stays pure.
    """
    slug = _ROOM_SAFE.sub("-", agent_id).strip("-")[:48] or "agent"
    return f"vg-probe-{slug}-{nonce}"


async def probe_agent(
    creds,
    *,
    agent_id: str,
    dispatch_name: str,
    nonce: str,
    trials: int = 1,
    warmup: bool = True,
    store: Any = None,
) -> dict[str, Any]:
    """Place one real call to a single agent and report what it actually cost.

    ``dispatch_name`` is the LiveKit ``Job.agent_name`` previously OBSERVED for
    this agent. A non-empty name is dispatched explicitly. The empty string means
    the worker registered without an agent_name, so it is on automatic dispatch:
    creating the room is the whole dispatch, and any other automatic-dispatch
    worker online may answer instead. The caller is responsible for having
    observed the name; nothing here invents one.

    Every number returned is measured: end-to-end timing comes from the synthetic
    client, the STT/LLM/TTS split from the rows the agent itself wrote for this
    room, and ``cost_usd`` is the sum of those same rows' costs. Anything that
    could not be measured comes back None rather than zero.
    """
    d = _diag()
    trials = min(max(1, int(trials)), MAX_PROBE_TRIALS)
    room = probe_room_name(agent_id, nonce)
    explicit = bool(dispatch_name)

    admin = d.LiveKitAdmin(creds)
    admin.url = creds.url
    try:
        runner = d.ProbeRunner(
            admin,
            lambda u, t: d.SyntheticClient(creds.url, t),
            d.UtteranceSource(_utterance_path()),
            _component_reader(store),
        )
        result = await runner.probe(
            dispatch_name,
            trials,
            warmup,
            room,
            "",
            dispatch=explicit,
        )
    finally:
        await admin.aclose()

    stats = d.summarize(result)
    # Prefer the client/dispatch error (the call never connected); else surface
    # what the agent itself logged for this room. The synthetic client only sees
    # "no reply"; the reason (an STT/LLM/TTS that errored, e.g. a 401 to the model
    # gateway) is in the rows the agent wrote. Read by the FIXED room, not
    # result.room (which is None when no turn completed, exactly the failure case
    # where the cause matters most).
    error = result.error or await _probe_error(store, room)
    return {
        "agent_id": agent_id,
        "dispatch_name": dispatch_name,
        "mode": "explicit" if explicit else "automatic",
        "room": result.room,
        "trials": stats["trials"],
        # Seconds, like every other number the probe reports.
        "e2e": stats if stats["trials"] else None,
        "components": result.components,
        "cost_usd": await _probe_cost(store, result.room),
        "models": await _probe_models(store, result.room),
        "error": error,
    }


async def _probe_cost(store: Any, room: str | None) -> float | None:
    """Sum ``cost_usd`` over the rows this probe's room produced.

    None (not 0.0) when the cost is unknowable here: no store, no room, or an
    agent that ships its telemetry to a remote collector so nothing landed
    locally. A zero would read as "this call was free", which is a different and
    false claim.
    """
    if store is None or not room:
        return None
    try:
        rows = await store.get_requests_for_room(room)
    except Exception:  # noqa: BLE001 - a read-back failure must not fail the probe
        return None
    if not rows:
        return None
    return sum(float(r.get("cost_usd") or 0.0) for r in rows)


async def _probe_models(store: Any, room: str | None) -> dict[str, str | None]:
    """The STT/LLM/TTS model this probe actually ran, for the split's hover labels.

    Read from the same rows as the cost/split so the label matches the measured
    call exactly. None per leg the call did not produce (or that this host did not
    see), never a guess.
    """
    out: dict[str, str | None] = {"stt": None, "llm": None, "tts": None}
    if store is None or not room:
        return out
    try:
        rows = await store.get_requests_for_room(room)
    except Exception:  # noqa: BLE001 - a read-back failure must not fail the probe
        return out
    for r in rows:
        modality = r.get("modality")
        model = r.get("model_id")
        if modality in out and model:
            out[modality] = model
    return out


async def _probe_error(store: Any, room: str | None) -> str | None:
    """A one-line summary of any agent-side error rows for this probe's room.

    ``attach``'s error handler writes a ``status="error"`` row per failed
    STT/LLM/TTS call, carrying the provider's message. When the probe measured
    nothing because the agent's pipeline errored (rather than because the agent
    never joined), this is the cause: surfacing it turns a bland "not measured"
    into "STT: 401 Unauthorized" that the operator can act on. Deduped and
    capped so a retry storm does not flood the card.
    """
    if store is None or not room:
        return None
    try:
        rows = await store.get_requests_for_room(room)
    except Exception:  # noqa: BLE001 - a read-back failure must not fail the probe
        return None
    labels: list[str] = []
    for r in rows:
        if r.get("status") != "error":
            continue
        modality = (r.get("modality") or "").upper()
        message = r.get("error_message") or "error"
        label = f"{modality}: {message}" if modality else message
        if label not in labels:
            labels.append(label)
    if not labels:
        return None
    return "; ".join(labels[:3])


async def execute_run(
    checks: list[str], config: dict[str, Any], creds, *, probes
) -> dict[str, Any]:
    """Run each requested check under isolation; return results, gates + verdict.

    The verdict comes from :mod:`voicegateway.livekit_diag.gates`, which is the
    ONLY place either surface decides whether a run is healthy. This function
    used to own a second, more lenient implementation (``_verdict``): it called a
    probe that measured nothing a PASS, downgraded a ``Poor``/``Lost`` SFU
    baseline to WARN, and never read an ``sfu_load`` baseline at all. ``gates``
    carries the strict reading of each of those; see its module docstring for
    the full disagreement table.

    ``gates`` is additive on the wire: the dashboard reads ``verdict`` exactly as
    before, and the per-gate detail is there for anything that wants to say WHY.
    """
    results: dict[str, Any] = {}
    for check in checks:
        try:
            if check == "agents":
                coro = probes.agents(creds)
            elif check == "sfu":
                coro = probes.sfu(creds, False, config)
            elif check == "sfu_load":
                coro = probes.sfu(creds, True, config)
            elif check == "latency":
                coro = probes.latency(creds, config)
            else:
                continue
            res = await asyncio.wait_for(coro, PER_CHECK_TIMEOUT_SECONDS)
            results[check] = {"ok": True, "result": res}
        except TimeoutError:
            results[check] = {"ok": False, "error": "check timed out"}
        except Exception as exc:  # noqa: BLE001 - a check must not kill the run
            results[check] = {"ok": False, "error": str(exc)}
    gate_results = gates.evaluate_checks(
        results, _safe_float(config.get("target_ms", 1500), 1500.0)
    )
    return {
        "checks": results,
        "gates": [g.as_dict() for g in gate_results],
        "verdict": gates.verdict(gate_results),
    }
