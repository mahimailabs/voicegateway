"""The play button: POST /api/agents/{id}/probe, and the eligibility block.

A probe places one real call through the agent's real cascade, so every press is
billed. These tests cover the gates that stand in front of that call (admin
scope, storage, LiveKit creds, an observed dispatch name, one in flight, a
cooldown) and the eligibility block the fleet index ships so the UI can grey the
button out with a reason instead of letting a press fail.

``probe_agent`` itself is faked here: it is exercised for real in
tests/livekit_diag/test_probe_agent.py. What is under test is the endpoint's
decisions, including that a result is passed through verbatim (nulls and all)
rather than being padded with zeros.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.auth import ApiKey
from voicegateway.core.gateway import Gateway
from voicegateway.livekit_diag.config import CredsError, LiveKitCreds
from voicegateway.models.request_model import RequestRecord
from voicegateway.server import build_app
from voicegateway.server.api.dashboard import agents

_CREDS = LiveKitCreds("wss://fake", "key", "secret")

# Sentinel so _seed_roster can tell "dispatch_name not passed" (default to the
# display name) from an explicit None (a Pipecat worker with no dispatch name).
_UNSET_ARG = object()

# What a probe returns when everything was measurable. The endpoint must not
# reshape it. Keep in step with test_result_shape_is_pinned in
# tests/livekit_diag/test_probe_agent.py, which asserts the real key set: this
# is a fake, so drift here is invisible to every test in this file.
_SAMPLE = {
    "agent_id": "support",
    "dispatch_name": "support-bot",
    "room": "vg-probe-support-ab12cd34",
    "mode": "explicit",
    "trials": 1,
    "e2e": {
        "avg": 1.25,
        "p50": 1.25,
        "p95": 1.25,
        "min": 1.25,
        "max": 1.25,
        "trials": 1,
    },
    "components": {"stt": 0.18, "llm_ttft": 0.48, "tts": 0.21},
    "cost_usd": 0.0052,
    "error": None,
}


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """The single-flight set and cooldown map are module globals (one dashboard
    process, one fleet), so they leak between tests unless cleared."""
    agents._PROBES_INFLIGHT.clear()
    agents._PROBE_LAST_RUN.clear()
    yield
    agents._PROBES_INFLIGHT.clear()
    agents._PROBE_LAST_RUN.clear()


def _gateway(tmp_path, monkeypatch) -> Gateway:
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "probe.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump({"cost_tracking": {"enabled": True}}))
    return Gateway(config_path=str(path))


def _gateway_without_storage(tmp_path, monkeypatch) -> Gateway:
    """Cost tracking off AND no VOICEGW_DB_PATH: the env var alone would give
    the Gateway a store back, which is not the configuration under test."""
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    path = tmp_path / "no-storage.yaml"
    path.write_text(yaml.dump({"cost_tracking": {"enabled": False}}))
    return Gateway(config_path=str(path))


def _client(gw: Gateway, api_keys: list[ApiKey] | None = None) -> AsyncClient:
    app = build_app(gw)
    if api_keys is not None:
        app.state.api_keys = api_keys
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_dispatch(gw: Gateway, agent_id: str, name: str, ts: float = 1.0):
    """Write the one row the endpoint reads: a call this agent actually ran."""
    await gw.storage.log_request(
        RequestRecord(
            id=f"{agent_id}-{ts}",
            timestamp=ts,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="default",
            agent_id=agent_id,
            metadata={"dispatch_name": name, "room": "call-1"},
        )
    )


async def _seed_roster(
    gw: Gateway, agent_id: str, name: str, dispatch_name: str | None = _UNSET_ARG
) -> None:
    """Register a live worker in the roster, the way register_worker's heartbeat
    does: this is the fallback source for an agent that has come online but not
    yet served an observed call. ``dispatch_name`` defaults to ``name`` (a worker
    registered under a name dispatches by it); pass ``None`` for a worker with no
    LiveKit dispatch (a Pipecat agent), which stays in the roster but not
    probeable."""
    from sqlalchemy import text

    dn = name if dispatch_name is _UNSET_ARG else dispatch_name
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        await db.execute(
            text(
                "INSERT INTO workers (agent_id, agent_name, dispatch_name, "
                "project, status, last_seen) VALUES (:a, :n, :dn, 'default', "
                "'idle', :now)"
            ),
            {"a": agent_id, "n": name, "dn": dn, "now": time.time()},
        )
        await db.commit()


def _configured(monkeypatch) -> None:
    monkeypatch.setattr(agents, "_resolve_creds", lambda: _CREDS)


def _unconfigured(monkeypatch) -> None:
    def _raise():
        raise CredsError("no creds")

    monkeypatch.setattr(agents, "_resolve_creds", _raise)


def _patch_probe(monkeypatch, fn, *, timeout: float = 120.0) -> None:
    """Swap the whole diag namespace so the real service module is untouched."""
    monkeypatch.setattr(
        agents,
        "diag_service",
        SimpleNamespace(probe_agent=fn, PROBE_TIMEOUT_SECONDS=timeout),
    )


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_probe_returns_the_measured_sample_verbatim(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    _configured(monkeypatch)
    _patch_probe(monkeypatch, lambda *a, **kw: _echo(_SAMPLE))
    async with _client(gw) as c:
        resp = await c.post("/api/agents/support/probe")
    assert resp.status_code == 200
    assert resp.json() == _SAMPLE


async def _echo(value: Any) -> Any:
    return value


async def test_probe_caches_result_and_index_exposes_latency_probe(
    tmp_path, monkeypatch
):
    """A probe caches its result; the fleet index then serves it as latency_probe
    so the Agents page renders the split without re-running a billed probe."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    await _insert_obs(gw, "support")  # so it appears on the index
    _configured(monkeypatch)
    _patch_probe(monkeypatch, lambda *a, **kw: _echo(_SAMPLE))
    async with _client(gw) as c:
        await c.post("/api/agents/support/probe")
        listing = (await c.get("/api/agents")).json()["agents"]
    entry = next(a for a in listing if a["agent_id"] == "support")
    lp = entry["latency_probe"]
    assert lp is not None
    assert lp["components"] == _SAMPLE["components"]
    assert lp["cost_usd"] == _SAMPLE["cost_usd"]
    assert lp["created_at"] > 0


async def test_index_latency_probe_is_null_before_any_probe(tmp_path, monkeypatch):
    """An agent that has never been probed has no cached latency graph."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    await _insert_obs(gw, "support")
    _configured(monkeypatch)
    async with _client(gw) as c:
        listing = (await c.get("/api/agents")).json()["agents"]
    entry = next(a for a in listing if a["agent_id"] == "support")
    assert entry["latency_probe"] is None


async def test_probe_dispatches_to_the_observed_name(tmp_path, monkeypatch):
    """The name is read back from telemetry, never chosen by the dashboard."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    _configured(monkeypatch)
    seen: dict[str, Any] = {}

    async def _capture(creds, **kw):
        seen.update(kw)
        seen["creds"] = creds
        return _SAMPLE

    _patch_probe(monkeypatch, _capture)
    async with _client(gw) as c:
        await c.post("/api/agents/support/probe")
    assert seen["dispatch_name"] == "support-bot"
    assert seen["agent_id"] == "support"
    assert seen["creds"] is _CREDS
    assert seen["store"] is gw.storage  # so cost can be read back
    assert len(seen["nonce"]) == 8  # unique room per press


async def test_probe_falls_back_to_the_roster_name(tmp_path, monkeypatch):
    """No observed job, but a live worker in the roster: the endpoint dispatches
    to the name that worker registered with instead of refusing."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_roster(gw, "reception", "reception")
    _configured(monkeypatch)
    seen: dict[str, Any] = {}

    async def _capture(creds, **kw):
        seen.update(kw)
        return _SAMPLE

    _patch_probe(monkeypatch, _capture)
    async with _client(gw) as c:
        resp = await c.post("/api/agents/reception/probe")
    assert resp.status_code == 200
    assert seen["dispatch_name"] == "reception"
    assert seen["agent_id"] == "reception"


async def test_probe_refuses_a_worker_with_no_dispatch_name(tmp_path, monkeypatch):
    """A roster row whose dispatch_name is None (a Pipecat worker) has no name to
    dispatch by, so the endpoint refuses rather than dispatch the display label."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_roster(gw, "pipecat-bot", "pipecat-bot", dispatch_name=None)
    _configured(monkeypatch)
    called = False

    async def _never(*a, **kw):
        nonlocal called
        called = True
        return _SAMPLE

    _patch_probe(monkeypatch, _never)
    async with _client(gw) as c:
        resp = await c.post("/api/agents/pipecat-bot/probe")
    assert resp.status_code == 400
    assert called is False


async def test_probe_prefers_the_observed_name_over_the_roster(tmp_path, monkeypatch):
    """Both sources present: the endpoint dispatches to the proven name."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "observed-name")
    await _seed_roster(gw, "support", "roster-name")
    _configured(monkeypatch)
    seen: dict[str, Any] = {}

    async def _capture(creds, **kw):
        seen.update(kw)
        return _SAMPLE

    _patch_probe(monkeypatch, _capture)
    async with _client(gw) as c:
        await c.post("/api/agents/support/probe")
    assert seen["dispatch_name"] == "observed-name"


async def test_an_automatic_dispatch_agent_is_probeable(tmp_path, monkeypatch):
    """An empty name is an answer (automatic dispatch), not a missing one."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "auto", "")
    _configured(monkeypatch)
    seen: dict[str, Any] = {}

    async def _capture(creds, **kw):
        seen.update(kw)
        return _SAMPLE

    _patch_probe(monkeypatch, _capture)
    async with _client(gw) as c:
        resp = await c.post("/api/agents/auto/probe")
    assert resp.status_code == 200
    assert seen["dispatch_name"] == ""


async def test_unmeasurable_numbers_stay_null(tmp_path, monkeypatch):
    """A remote-collector agent wrote no rows here. $0.00 would be a lie."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "remote", "remote-bot")
    _configured(monkeypatch)
    degraded = {**_SAMPLE, "components": None, "cost_usd": None}
    _patch_probe(monkeypatch, lambda *a, **kw: _echo(degraded))
    async with _client(gw) as c:
        body = (await c.post("/api/agents/remote/probe")).json()
    assert body["cost_usd"] is None
    assert body["components"] is None
    assert body["e2e"]["avg"] == 1.25


# ---------------------------------------------------------------------------
# Refusals: nothing to measure with
# ---------------------------------------------------------------------------


async def test_probe_refuses_when_storage_is_disabled(tmp_path, monkeypatch):
    gw = _gateway_without_storage(tmp_path, monkeypatch)
    assert gw.storage is None
    _configured(monkeypatch)
    async with _client(gw) as c:
        resp = await c.post("/api/agents/support/probe")
    assert resp.status_code == 400
    assert "storage is disabled" in resp.json()["detail"]


async def test_probe_refuses_when_livekit_is_not_configured(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    _unconfigured(monkeypatch)
    async with _client(gw) as c:
        resp = await c.post("/api/agents/support/probe")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "LiveKit not configured"


async def test_probe_refuses_an_agent_with_no_observed_job(tmp_path, monkeypatch):
    """No observed job AND no live worker in the roster means no name to
    dispatch by. The daemon dispatches by the agent_name a worker registers
    with; with neither source it has nothing, so it refuses before any billed
    traffic rather than inventing a name that would resolve to no worker.
    """
    gw = _gateway(tmp_path, monkeypatch)
    await gw.storage._ensure_initialized()
    _configured(monkeypatch)
    called = False

    async def _never(*a, **kw):
        nonlocal called
        called = True
        return _SAMPLE

    _patch_probe(monkeypatch, _never)
    async with _client(gw) as c:
        resp = await c.post("/api/agents/ghost/probe")
    assert resp.status_code == 400
    assert "no LiveKit job observed" in resp.json()["detail"]
    assert called is False  # refused before any billed traffic


# ---------------------------------------------------------------------------
# Rate limiting: every press is a billed call
# ---------------------------------------------------------------------------


async def test_a_second_press_inside_the_cooldown_is_throttled(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    _configured(monkeypatch)
    calls = 0

    async def _count(*a, **kw):
        nonlocal calls
        calls += 1
        return _SAMPLE

    _patch_probe(monkeypatch, _count)
    async with _client(gw) as c:
        assert (await c.post("/api/agents/support/probe")).status_code == 200
        resp = await c.post("/api/agents/support/probe")
    assert resp.status_code == 429
    assert calls == 1  # the throttled press placed no call
    # The client is told when it may retry, not left to guess.
    assert 0 < int(resp.headers["Retry-After"]) <= agents.PROBE_COOLDOWN_SECONDS + 1


async def test_the_cooldown_is_per_agent(tmp_path, monkeypatch):
    """Probing one agent must not throttle the button on another card."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "a", "a-bot")
    await _seed_dispatch(gw, "b", "b-bot", ts=2.0)
    _configured(monkeypatch)
    _patch_probe(monkeypatch, lambda *a, **kw: _echo(_SAMPLE))
    async with _client(gw) as c:
        assert (await c.post("/api/agents/a/probe")).status_code == 200
        assert (await c.post("/api/agents/b/probe")).status_code == 200


async def test_a_concurrent_press_is_rejected_while_one_is_in_flight(
    tmp_path, monkeypatch
):
    """The single-flight lock outlives the cooldown.

    A probe may run for the full timeout (120s), which is longer than the 30s
    cooldown, so the window where the cooldown has lapsed but a call is still
    up is real. That is the 409.
    """
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    _configured(monkeypatch)
    _patch_probe(monkeypatch, lambda *a, **kw: _echo(_SAMPLE))
    agents._PROBES_INFLIGHT.add("support")
    agents._PROBE_LAST_RUN["support"] = time.monotonic() - 100  # cooldown lapsed
    async with _client(gw) as c:
        resp = await c.post("/api/agents/support/probe")
    assert resp.status_code == 409
    assert "already running" in resp.json()["detail"]


async def test_the_lock_is_released_after_a_press(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    _configured(monkeypatch)
    _patch_probe(monkeypatch, lambda *a, **kw: _echo(_SAMPLE))
    async with _client(gw) as c:
        await c.post("/api/agents/support/probe")
    assert agents._PROBES_INFLIGHT == set()


async def test_a_hung_probe_times_out_without_wedging_the_agent(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    await _insert_obs(gw, "support")  # so it appears on the index
    _configured(monkeypatch)

    async def _hang(*a, **kw):
        await asyncio.sleep(30)

    _patch_probe(monkeypatch, _hang, timeout=0.05)
    async with _client(gw) as c:
        resp = await c.post("/api/agents/support/probe")
        assert resp.status_code == 504
        # A timeout is cached as an honest error too, so the page shows "errored"
        # rather than a stale graph from an earlier good probe.
        listing = (await c.get("/api/agents")).json()["agents"]
    entry = next(a for a in listing if a["agent_id"] == "support")
    lp = entry["latency_probe"]
    assert lp is not None
    assert lp["components"] is None
    assert lp["error"] == "probe timed out"
    # The lock is released, so the agent is not probeable-never-again...
    assert agents._PROBES_INFLIGHT == set()
    # ...but the cooldown was stamped BEFORE the call, so a hung probe cannot be
    # retried instantly: the cooldown is about how often calls are placed.
    assert agents._PROBE_LAST_RUN["support"] is not None


async def test_a_failing_probe_caches_the_error_and_releases_the_lock(
    tmp_path, monkeypatch
):
    """A crash is caught, cached as an honest "errored" state, and surfaced as a
    502, so the page never shows a stale graph and the agent stays probeable."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    await _insert_obs(gw, "support")  # so it appears on the index
    _configured(monkeypatch)

    async def _boom(*a, **kw):
        raise RuntimeError("livekit exploded")

    _patch_probe(monkeypatch, _boom)
    async with _client(gw) as c:
        resp = await c.post("/api/agents/support/probe")
        # The crash is surfaced, not swallowed into a tidy 200...
        assert resp.status_code == 502
        assert "livekit exploded" in resp.json()["detail"]
        # ...and cached as an honest failure the index then serves, so the page
        # shows "errored" instead of the previous good probe's stale split.
        listing = (await c.get("/api/agents")).json()["agents"]
    entry = next(a for a in listing if a["agent_id"] == "support")
    lp = entry["latency_probe"]
    assert lp is not None
    assert lp["components"] is None  # nothing was measured
    assert "livekit exploded" in lp["error"]
    # The lock is released, so the agent is not left permanently un-probeable.
    assert agents._PROBES_INFLIGHT == set()


async def test_an_unexpected_failure_is_a_502_and_still_costs_the_cooldown(
    tmp_path, monkeypatch
):
    """What reaches the operator when the probe blows up part-way through.

    A crash the probe service could not name is caught, cached as an honest
    "errored" state (so the page never shows a stale graph), and surfaced as a
    502 carrying the reason. The traceback still lands in the logs; it is not
    swallowed into a tidy 200.

    The cooldown survives the failure on purpose. The stamp is written before
    the call, and an exception raised part-way through may well follow a call
    that was already placed and billed. Clearing it would re-offer a press that
    could charge twice, so the wait stands either way.
    """
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    _configured(monkeypatch)

    async def _boom(*a, **kw):
        raise RuntimeError("livekit exploded")

    _patch_probe(monkeypatch, _boom)
    async with _client(gw) as c:
        first = await c.post("/api/agents/support/probe")
        assert first.status_code == 502
        assert "livekit exploded" in first.json()["detail"]
        assert (await c.post("/api/agents/support/probe")).status_code == 429


# ---------------------------------------------------------------------------
# Auth: the gate is a no-op locally and real once keys exist
# ---------------------------------------------------------------------------


async def test_probe_is_open_when_no_api_keys_are_configured(tmp_path, monkeypatch):
    """The local single-operator default: no keys, no gate."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    _configured(monkeypatch)
    _patch_probe(monkeypatch, lambda *a, **kw: _echo(_SAMPLE))
    async with _client(gw, api_keys=[]) as c:
        assert (await c.post("/api/agents/support/probe")).status_code == 200


async def test_probe_needs_a_token_once_api_keys_are_configured(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    _configured(monkeypatch)
    called = False

    async def _never(*a, **kw):
        nonlocal called
        called = True
        return _SAMPLE

    _patch_probe(monkeypatch, _never)
    keys = [ApiKey(token="secret", name="ops", scopes=("*",))]
    async with _client(gw, api_keys=keys) as c:
        assert (await c.post("/api/agents/support/probe")).status_code == 401
        ok = await c.post(
            "/api/agents/support/probe",
            headers={"Authorization": "Bearer secret"},
        )
    assert ok.status_code == 200
    assert called is True


async def test_a_read_only_token_cannot_place_a_billed_call(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    _configured(monkeypatch)
    _patch_probe(monkeypatch, lambda *a, **kw: _echo(_SAMPLE))
    keys = [ApiKey(token="ro", name="viewer", scopes=("read",))]
    async with _client(gw, api_keys=keys) as c:
        resp = await c.post(
            "/api/agents/support/probe", headers={"Authorization": "Bearer ro"}
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# The eligibility block on GET /api/agents
# ---------------------------------------------------------------------------


async def _insert_obs(gw: Gateway, agent_id: str) -> None:
    """The index reads the 24h rollup, so an agent needs a row there to appear.

    The dispatch name is read from ``requests`` separately (and unwindowed: it
    is a property of how the worker registered, not of recent traffic).
    """
    from sqlalchemy import text

    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        await db.execute(
            text(
                "INSERT INTO agent_observations (agent_id, request_count, "
                "total_cost_usd, error_count, last_seen, window_start, "
                "window_end) VALUES (:a, 1, 0.0, 0, :now, 'ws', 'we')"
            ),
            {"a": agent_id, "now": time.time()},
        )
        await db.commit()


async def _entry(gw: Gateway, agent_id: str) -> dict[str, Any]:
    async with _client(gw) as c:
        listing = (await c.get("/api/agents")).json()["agents"]
    return next(a for a in listing if a["agent_id"] == agent_id)


async def test_index_reports_a_named_agent_as_explicitly_dispatchable(
    tmp_path, monkeypatch
):
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    await _insert_obs(gw, "support")
    _configured(monkeypatch)
    probe = (await _entry(gw, "support"))["probe"]
    assert probe == {
        "eligible": True,
        "dispatch_name": "support-bot",
        "mode": "explicit",
        "reason": None,
    }


async def test_index_reports_a_lone_automatic_worker_without_caveat(
    tmp_path, monkeypatch
):
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "auto", "")
    await _insert_obs(gw, "auto")
    _configured(monkeypatch)
    probe = (await _entry(gw, "auto"))["probe"]
    assert probe["eligible"] is True
    assert probe["mode"] == "automatic"
    assert probe["reason"] is None


async def test_index_admits_ambiguity_with_two_automatic_workers(tmp_path, monkeypatch):
    """Two workers on automatic dispatch both join every new room.

    Whichever grabs the job answers, so the probe cannot claim to have measured
    a specific one. It stays eligible (the numbers are real) but says so.
    """
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "auto-a", "")
    await _seed_dispatch(gw, "auto-b", "", ts=2.0)
    await _insert_obs(gw, "auto-a")
    await _insert_obs(gw, "auto-b")
    _configured(monkeypatch)
    probe = (await _entry(gw, "auto-a"))["probe"]
    assert probe["eligible"] is True
    assert "may answer" in probe["reason"]
    assert "2 agents" in probe["reason"]
    # The count is of workers OBSERVED on automatic dispatch. This host cannot
    # see which are running, so the caveat must not assert that they are.
    assert "are online" not in probe["reason"]


async def test_index_marks_a_never_observed_agent_ineligible(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch)
    # Telemetry with no dispatch_name: seen, but never on an observed job.
    await gw.storage.log_request(
        RequestRecord(
            id="r1",
            timestamp=time.time(),
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="default",
            agent_id="quiet",
            metadata={"room": "call-1"},
        )
    )
    await _insert_obs(gw, "quiet")
    _configured(monkeypatch)
    probe = (await _entry(gw, "quiet"))["probe"]
    assert probe["eligible"] is False
    assert probe["dispatch_name"] is None
    assert "no LiveKit job observed" in probe["reason"]


async def test_index_blames_missing_livekit_creds_first(tmp_path, monkeypatch):
    """Without creds nothing is probeable, and the reason names the fix."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "support-bot")
    await _insert_obs(gw, "support")
    _unconfigured(monkeypatch)
    probe = (await _entry(gw, "support"))["probe"]
    assert probe["eligible"] is False
    assert "LIVEKIT_URL" in probe["reason"]


async def test_a_roster_only_worker_is_probeable_by_its_registered_name(
    tmp_path, monkeypatch
):
    """A worker that has heartbeated but never served an observed call is still
    probeable: register_worker's agent_name is what LiveKit dispatches by, so
    the button need not wait for a first call. The reason flags the name as not
    yet confirmed against a completed job."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_roster(gw, "idle-bot", "idle-bot")
    _configured(monkeypatch)
    probe = (await _entry(gw, "idle-bot"))["probe"]
    assert probe["eligible"] is True
    assert probe["dispatch_name"] == "idle-bot"
    assert probe["mode"] == "explicit"
    assert "registered" in probe["reason"]  # unverified-name caveat


async def test_a_worker_with_no_dispatch_name_is_not_probeable(tmp_path, monkeypatch):
    """A Pipecat worker (or any roster row with no LiveKit dispatch name) is in
    the roster but has nothing to dispatch by, so its card stays ineligible."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_roster(gw, "pipecat-bot", "pipecat-bot", dispatch_name=None)
    _configured(monkeypatch)
    probe = (await _entry(gw, "pipecat-bot"))["probe"]
    assert probe["eligible"] is False
    assert probe["dispatch_name"] is None


async def test_observed_name_wins_over_the_roster(tmp_path, monkeypatch):
    """When a completed job proved a name, that beats whatever the worker
    currently claims in the roster: the proven value is more trustworthy."""
    gw = _gateway(tmp_path, monkeypatch)
    await _seed_dispatch(gw, "support", "observed-name")
    await _seed_roster(gw, "support", "roster-name")
    await _insert_obs(gw, "support")
    _configured(monkeypatch)
    probe = (await _entry(gw, "support"))["probe"]
    assert probe["eligible"] is True
    assert probe["dispatch_name"] == "observed-name"
    assert probe["reason"] is None  # proven, so no caveat


def test_unattributed_traffic_is_not_probeable() -> None:
    """Rows with no agent_id belong to no agent, so there is nothing to call."""
    block = agents._probe_block(
        None, {}, {}, livekit_configured=True, automatic_count=0
    )
    assert block["eligible"] is False
    assert block["dispatch_name"] is None
    assert "unattributed" in block["reason"]
