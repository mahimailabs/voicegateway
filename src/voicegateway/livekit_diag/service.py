"""Bounded execution of the livekit_diag probes from resolved LiveKit creds.

RealProbes wraps LiveKitAdmin/ProbeRunner/SfuProbe exactly as the CLI does, with
hard caps applied. execute_run runs each requested check under try/except so one
failing check never kills the others (mirrors the CLI's best-effort behaviour).
Tests inject a fake `probes` object with the same async method surface.
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import Any

PER_CHECK_TIMEOUT_SECONDS = 120.0
MAX_LOAD_CLIENTS = 25
MAX_LOAD_SECONDS = 30.0
MAX_LATENCY_TRIALS = 3
_DEFAULT_RAMP = [2, 10, 25]
_TARGET_RTT_MS = 50.0
_MAX_LOSS = 1.0


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
        from voicegateway.livekit_diag.latency import ProbeRunner, summarize
        from voicegateway.livekit_diag.report import agents_json
        from voicegateway.livekit_diag.resources import ResourceMonitor
        from voicegateway.livekit_diag.sfu import SfuProbe, find_knee

        _diag_cache = SimpleNamespace(
            pkg=pkg,
            LiveKitAdmin=LiveKitAdmin,
            SyntheticClient=SyntheticClient,
            UtteranceSource=UtteranceSource,
            ProbeRunner=ProbeRunner,
            summarize=summarize,
            agents_json=agents_json,
            ResourceMonitor=ResourceMonitor,
            SfuProbe=SfuProbe,
            find_knee=find_knee,
        )
    return _diag_cache


def _utterance_path() -> str:
    return str(pathlib.Path(_diag().pkg.__file__).parent / "assets" / "probe.wav")


class RealProbes:
    """Runs the engine probes against a live LiveKit server."""

    async def agents(self, creds) -> dict[str, Any]:
        d = _diag()
        admin = d.LiveKitAdmin(creds)
        admin.url = creds.url
        try:
            rows = await admin.list_agents()
        finally:
            await admin.aclose()
        return {"agents": d.agents_json(rows)}

    async def sfu(self, creds, load: bool, config: dict[str, Any]) -> dict[str, Any]:
        d = _diag()
        admin = d.LiveKitAdmin(creds)
        admin.url = creds.url
        probe = d.SfuProbe(
            admin, lambda u, t: d.SyntheticClient(u, t), d.ResourceMonitor()
        )
        try:
            base = await probe.baseline("vg-diag-sfu")
            steps, _resource = ([], None)
            if load:
                steps, _resource = await probe.ramp(
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
            },
            "ramp": [
                {"clients": s.clients, "rtt_ms": s.rtt_ms, "loss_pct": s.loss_pct}
                for s in steps
            ],
            "knee": knee,
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


def _verdict(check_results: dict[str, Any], target_ms: float) -> str:
    if any(not r["ok"] for r in check_results.values()):
        return "FAIL"
    lat = check_results.get("latency")
    if lat and lat["ok"]:
        for a in lat["result"].get("agents", []):
            if (a.get("stats", {}).get("avg", 0) * 1000) > target_ms:
                return "WARN"
    sfu = check_results.get("sfu")
    if sfu and sfu["ok"]:
        b = sfu["result"].get("baseline", {})
        if b.get("loss_pct", 0) > 1.0 or b.get("quality") in {"Poor", "Lost"}:
            return "WARN"
    return "PASS"


async def execute_run(
    checks: list[str], config: dict[str, Any], creds, *, probes
) -> dict[str, Any]:
    """Run each requested check under isolation; return results + verdict."""
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
    return {
        "checks": results,
        "verdict": _verdict(results, config.get("target_ms", 1500)),
    }
