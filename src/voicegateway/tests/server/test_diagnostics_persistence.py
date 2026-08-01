"""Diagnostics runs survive the process that started them -- with no wire change.

Two claims, tested separately:

1. **Persistence works.** A run is written at every state transition, so it is
   readable after the in-process registry is gone (a restart), and the history is
   no longer capped at the process-local 20.
2. **The payload did not move.** The endpoint contract is what the dashboard
   parses, and the demo fixtures were written against these exact shapes. The
   check here is a byte comparison: the raw response for a run served from memory
   must equal the raw response for the same run rehydrated from the table, and
   the field list is frozen against the pre-persistence payload.

A cleared ``_RUNS``/``_ORDER`` is how a restart is simulated: those two are the
whole of the in-process state, so emptying them leaves exactly what a fresh
process would see.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.livekit_diag.config import LiveKitCreds
from voicegateway.server.main import build_app

# The exact payload fields, in order, that GET /runs and GET /runs/{id} returned
# before runs were persisted (diagnostics._as_dict at commit 94a2d0a). Frozen here
# so a renamed or reordered field fails a test instead of silently blanking a
# column in the dashboard.
_RUN_FIELDS = [
    "run_id",
    "status",
    "checks",
    "config",
    "results",
    "verdict",
    "error",
    "created_at",
    "started_at",
    "ended_at",
]

_FAKE_CREDS = LiveKitCreds("wss://x", "k", "s")


class _FakeProbes:
    """Probe output shaped like the real thing: nested dicts, lists, floats and a
    ``None`` knee -- the values a JSON round-trip could plausibly move."""

    async def agents(self, creds: Any) -> dict[str, Any]:
        return {"agents": [{"name": "a1", "count": 2}]}

    async def sfu(
        self, creds: Any, load: bool, config: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "baseline": {"rtt_ms": 5.5, "loss_pct": 0.0, "quality": "Excellent"},
            "ramp": [{"participants": 2, "rtt_ms": 6.25}],
            "knee": None,
        }

    async def latency(self, creds: Any, config: dict[str, Any]) -> dict[str, Any]:
        return {"agents": []}


class _SlowProbes(_FakeProbes):
    """Blocks long enough that a run is observably still in flight."""

    async def agents(self, creds: Any) -> dict[str, Any]:
        await asyncio.sleep(0.2)
        return {"agents": []}


class _BoomProbes(_FakeProbes):
    async def agents(self, creds: Any) -> dict[str, Any]:
        raise RuntimeError("probe exploded")


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "diag-persist.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
def storageless_gateway(tmp_path, monkeypatch):
    """A Gateway with cost-tracking storage disabled (``gateway.storage`` None)."""
    config_path = tmp_path / "no-storage.yaml"
    config_path.write_text(
        yaml.dump({"providers": {}, "cost_tracking": {"enabled": False}})
    )
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    monkeypatch.delenv("VOICEGW_DB_URL", raising=False)
    gw = Gateway(config_path=str(config_path))
    assert gw.storage is None, "fixture precondition: storage must be disabled"
    return gw


def _client(gw: Gateway) -> AsyncClient:
    app = build_app(gw, enable_mcp_sse=False, enable_dashboard=True)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def client(gateway):
    async with _client(gateway) as c:
        yield c


@pytest.fixture(autouse=True)
def _fake_probes(monkeypatch):
    from voicegateway.server.api.dashboard import diagnostics as d

    monkeypatch.setattr(d, "_resolve_creds", lambda: _FAKE_CREDS)
    monkeypatch.setattr(d, "_make_probes", lambda _store: _FakeProbes())


@pytest.fixture(autouse=True)
def _clear_registry():
    from voicegateway.server.api.dashboard import diagnostics as d

    d._RUNS.clear()
    d._ORDER.clear()
    d._TASKS.clear()
    yield
    d._RUNS.clear()
    d._ORDER.clear()
    d._TASKS.clear()


def _simulate_restart() -> None:
    """Drop every trace of the in-process registry (what a restart does)."""
    from voicegateway.server.api.dashboard import diagnostics as d

    d._RUNS.clear()
    d._ORDER.clear()
    d._TASKS.clear()


async def _drain(client: AsyncClient, run_id: str) -> dict[str, Any]:
    """Poll one run until it is terminal, so no task outlives the test."""
    for _ in range(400):
        got = await client.get(f"/api/diagnostics/runs/{run_id}")
        assert got.status_code == 200
        data = got.json()
        if data["status"] in ("done", "failed"):
            return data
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run_id} never reached a terminal state")


async def _start(client: AsyncClient, checks: list[str]) -> str:
    resp = await client.post(
        "/api/diagnostics/runs", json={"checks": checks, "config": {}}
    )
    assert resp.status_code == 200
    return str(resp.json()["run_id"])


async def _run_to_completion(client: AsyncClient, checks: list[str]) -> str:
    run_id = await _start(client, checks)
    await _drain(client, run_id)
    return run_id


# ---------------------------------------------------------------------------
# The contract did not move
# ---------------------------------------------------------------------------


async def test_payload_fields_are_exactly_the_pre_persistence_set(client) -> None:
    run_id = await _run_to_completion(client, ["agents"])

    single = (await client.get(f"/api/diagnostics/runs/{run_id}")).json()
    assert list(single) == _RUN_FIELDS

    listed = (await client.get("/api/diagnostics/runs")).json()
    assert isinstance(listed, list)
    assert list(listed[0]) == _RUN_FIELDS


async def test_post_and_creds_payloads_are_unchanged(client) -> None:
    created = await client.post(
        "/api/diagnostics/runs", json={"checks": ["agents"], "config": {}}
    )
    assert created.status_code == 200
    body = created.json()
    # The POST reply has always been exactly these two fields.
    assert list(body) == ["run_id", "status"]
    assert body["status"] == "queued"
    await _drain(client, body["run_id"])

    creds = await client.get("/api/diagnostics/creds")
    assert creds.status_code == 200
    assert creds.json() == {"configured": True, "url": "wss://x"}


async def test_rehydrated_run_serializes_to_identical_bytes(client) -> None:
    """The strongest form of "the contract held": the same bytes on the wire.

    Served from memory, then served from the table for the same run. A change to
    a field name, the field order, a number's formatting, or a null vs empty
    value would all show up as a byte difference here.
    """
    run_id = await _run_to_completion(client, ["agents", "sfu", "latency"])

    from_memory = await client.get(f"/api/diagnostics/runs/{run_id}")
    assert from_memory.status_code == 200

    _simulate_restart()

    from_storage = await client.get(f"/api/diagnostics/runs/{run_id}")
    assert from_storage.status_code == 200
    assert from_storage.content == from_memory.content


async def test_rehydrated_history_serializes_to_identical_bytes(client) -> None:
    await _run_to_completion(client, ["agents"])
    await _run_to_completion(client, ["sfu"])

    from_memory = await client.get("/api/diagnostics/runs")
    assert from_memory.status_code == 200
    assert len(from_memory.json()) == 2

    _simulate_restart()

    from_storage = await client.get("/api/diagnostics/runs")
    assert from_storage.status_code == 200
    assert from_storage.content == from_memory.content


async def test_unknown_run_is_still_404(client) -> None:
    resp = await client.get("/api/diagnostics/runs/doesnotexist")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def test_run_is_recorded_before_the_task_is_spawned(client, gateway) -> None:
    """The queued row exists the moment the POST returns.

    A run places billed calls; if the process dies between accepting the POST and
    the first probe, the history must still show that a run was started.
    """
    run_id = await _start(client, ["agents"])

    stored = await gateway.storage.get_diagnostics_run(run_id)
    assert stored is not None
    assert stored["status"] in ("queued", "running", "done")
    assert stored["checks"] == ["agents"]

    await _drain(client, run_id)


async def test_terminal_state_and_results_reach_storage(client, gateway) -> None:
    run_id = await _run_to_completion(client, ["agents", "sfu"])

    stored = await gateway.storage.get_diagnostics_run(run_id)
    assert stored is not None
    assert stored["status"] == "done"
    assert stored["verdict"] == "PASS"
    assert stored["started_at"] is not None
    assert stored["ended_at"] is not None
    assert "agents" in stored["results"]["checks"]
    # The probe payload is stored as returned: a None knee stays None, and the
    # hardcoded loss_pct is not repaired, dropped or re-derived on the way in.
    sfu = stored["results"]["checks"]["sfu"]["result"]
    assert sfu["knee"] is None
    assert sfu["baseline"]["loss_pct"] == 0.0


async def test_history_is_no_longer_capped_at_twenty(client, gateway) -> None:
    """The whole point of the table: ``_HISTORY_CAP`` bounds the working set only.

    Rows are seeded directly rather than by running 25 probes, which keeps the
    test about the read path.
    """
    for i in range(25):
        await gateway.storage.upsert_diagnostics_run(
            run_id=f"seed{i:02d}",
            checks=["agents"],
            config={},
            status="done",
            results={"verdict": "PASS", "checks": {}},
            verdict="PASS",
            created_at=f"2026-07-30T10:{i:02d}:00+00:00",
            started_at=f"2026-07-30T10:{i:02d}:01+00:00",
            ended_at=f"2026-07-30T10:{i:02d}:09+00:00",
        )

    rows = (await client.get("/api/diagnostics/runs")).json()
    assert len(rows) == 25
    assert rows[0]["run_id"] == "seed24", "newest first"
    assert rows[-1]["run_id"] == "seed00"


async def test_in_flight_run_is_served_from_memory_and_listed_once(
    client, monkeypatch
) -> None:
    """An active run is mutating between polls, so memory (not the row) answers.

    It must also appear in the history immediately, exactly once, before any
    terminal write -- the merge must not double-count a run that is in both
    places.
    """
    from voicegateway.server.api.dashboard import diagnostics as d

    monkeypatch.setattr(d, "_make_probes", lambda _store: _SlowProbes())

    run_id = await _start(client, ["agents"])

    live = (await client.get(f"/api/diagnostics/runs/{run_id}")).json()
    assert live["status"] in ("queued", "running")
    assert live["results"] is None

    listed = (await client.get("/api/diagnostics/runs")).json()
    assert [r["run_id"] for r in listed].count(run_id) == 1

    await _drain(client, run_id)


async def test_stored_copy_agrees_with_the_served_copy_on_failure(
    client, gateway, monkeypatch
) -> None:
    from voicegateway.server.api.dashboard import diagnostics as d

    monkeypatch.setattr(d, "_make_probes", lambda _store: _BoomProbes())

    run_id = await _run_to_completion(client, ["agents"])

    stored = await gateway.storage.get_diagnostics_run(run_id)
    served = (await client.get(f"/api/diagnostics/runs/{run_id}")).json()
    assert stored is not None
    # execute_run isolates a failing check, so the run itself completes with a
    # FAIL verdict. What matters here is that the stored copy says the same thing
    # the in-memory one does.
    assert stored["status"] == served["status"]
    assert stored["verdict"] == served["verdict"]
    assert stored["error"] == served["error"]
    assert stored["results"] == served["results"]


# ---------------------------------------------------------------------------
# Storage disabled / storage broken: behave as before the table existed
# ---------------------------------------------------------------------------


async def test_endpoints_work_with_storage_disabled(storageless_gateway) -> None:
    """No storage means the in-process registry is the whole history, as before.

    A dashboard run must not start requiring cost tracking to be enabled.
    """
    async with _client(storageless_gateway) as c:
        run_id = await _run_to_completion(c, ["agents"])

        single = (await c.get(f"/api/diagnostics/runs/{run_id}")).json()
        assert list(single) == _RUN_FIELDS
        assert single["status"] == "done"

        listed = (await c.get("/api/diagnostics/runs")).json()
        assert [r["run_id"] for r in listed] == [run_id]

        assert (await c.get("/api/diagnostics/runs/nope")).status_code == 404


async def test_a_storage_write_failure_does_not_kill_the_run(
    client, gateway, monkeypatch
) -> None:
    """A busy database must not abort a run that is already placing billed calls,
    nor 500 the POST that already accepted it."""

    async def _boom(**kwargs: Any) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(gateway.storage, "upsert_diagnostics_run", _boom)

    run_id = await _run_to_completion(client, ["agents"])
    served = (await client.get(f"/api/diagnostics/runs/{run_id}")).json()
    assert served["status"] == "done"
    assert served["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# Auth survived, and the coupled timeouts did not move
# ---------------------------------------------------------------------------


async def test_all_four_endpoints_are_admin_gated(gateway) -> None:
    """Persistence must not have dropped a gate. A run places billed calls, and
    the history now holds every run this deployment ever made.
    """
    from voicegateway.core.auth import ADMIN_SCOPE, ApiKey

    app = build_app(gateway, enable_mcp_sse=False, enable_dashboard=True)
    app.state.api_keys = [
        ApiKey(token="admin-secret-token", name="ops", scopes=(ADMIN_SCOPE,))
    ]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        assert (await c.get("/api/diagnostics/creds")).status_code == 401
        assert (await c.get("/api/diagnostics/runs")).status_code == 401
        assert (await c.get("/api/diagnostics/runs/anything")).status_code == 401
        blocked = await c.post(
            "/api/diagnostics/runs", json={"checks": ["agents"], "config": {}}
        )
        assert blocked.status_code == 401


def test_the_poll_budget_and_the_run_timeout_stay_coupled() -> None:
    """The dashboard polls 180 times at 2s; the server gives up at 360s.

    Those are the same six minutes expressed twice, in two languages. Raising one
    alone either strands the UI on a run still executing, or shows a timeout the
    server never reported. This node added run persistence only; the budget was
    left exactly where it was.
    """
    from voicegateway.server.api.dashboard import diagnostics as d

    assert d._OVERALL_RUN_TIMEOUT_SECONDS == 360.0

    page = (
        Path(d.__file__).resolve().parents[4]
        / "dashboard"
        / "frontend"
        / "src"
        / "pages"
        / "Diagnostics.tsx"
    )
    source = page.read_text(encoding="utf-8")
    assert "const MAX_POLLS = 180;" in source, (
        "the frontend poll budget moved (or the file did); it is coupled to "
        "_OVERALL_RUN_TIMEOUT_SECONDS and may only change with it"
    )
    assert "setTimeout(resolve, 2000)" in source, "the poll interval moved"
    assert 180 * 2000 / 1000 == d._OVERALL_RUN_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# A terminal status is durable BEFORE anyone can see it
# ---------------------------------------------------------------------------
#
# Both properties below were found by CI failing intermittently on py3.13 while
# 3.11 and 3.12 passed, which is what a race looks like from the outside. They
# are asserted directly here rather than left to timing.


class _OrderWatchingStore:
    """A store that records what the LIVE run object looked like at write time.

    This is what makes the ordering testable at all. Polling for "done" and then
    reading storage only catches the bug when the scheduler cooperates, which is
    exactly the intermittency that sent this to CI instead of to a test. Here the
    write itself reports whether the terminal status had already been published,
    so the assertion holds on every run.
    """

    def __init__(self, run) -> None:
        self._run = run
        self.writes: list[tuple[str, str]] = []

    async def upsert_diagnostics_run(self, **kw) -> None:
        # (what this write carries, what a reader of the live object sees now)
        self.writes.append((kw["status"], self._run.status))


async def test_a_terminal_status_is_stored_before_it_is_published(
    monkeypatch,
) -> None:
    """The race, pinned deterministically.

    ``_execute`` used to assign run.status = "done" and only then await the
    write. ``run`` is the object /runs serves, so between those two statements
    the API reported a finished run whose row still said "running", and anything
    keying off completion could act on a run the database did not agree was over.
    """
    from voicegateway.server.api.dashboard import diagnostics as d

    monkeypatch.setattr(d, "_make_probes", lambda _store: _FakeProbes())
    run = d._Run(
        run_id="r1", checks=["agents"], config={}, created_at="2026-08-01T00:00:00Z"
    )
    store = _OrderWatchingStore(run)

    await d._execute(run, _FAKE_CREDS, store)

    assert run.status == "done"
    terminal = [w for w in store.writes if w[0] in ("done", "failed")]
    assert terminal, f"no terminal write was made; got {store.writes}"
    written, visible_at_write_time = terminal[-1]
    assert visible_at_write_time != written, (
        "the terminal status was already visible to readers when it was written"
    )
    assert visible_at_write_time == "running"


async def test_the_row_is_complete_when_the_status_lands(monkeypatch) -> None:
    """A terminal row carrying no results is a run nobody can report on.

    The results and the status have to reach storage in the SAME write, or a
    reader that trusts the status finds a row it cannot build a report from.
    """
    from voicegateway.server.api.dashboard import diagnostics as d

    monkeypatch.setattr(d, "_make_probes", lambda _store: _FakeProbes())
    run = d._Run(
        run_id="r2",
        checks=["agents", "sfu"],
        config={},
        created_at="2026-08-01T00:00:00Z",
    )
    seen: list[dict] = []

    class _Recorder:
        async def upsert_diagnostics_run(self, **kw):
            seen.append(kw)

    await d._execute(run, _FAKE_CREDS, _Recorder())

    [terminal] = [w for w in seen if w["status"] == "done"]
    assert terminal["verdict"] is not None
    assert terminal["ended_at"] is not None
    assert "agents" in terminal["results"]["checks"]


async def test_a_visible_terminal_status_is_already_stored(client, gateway) -> None:
    """The same property end to end, through the real endpoint and storage.

    Kept alongside the deterministic test above because it exercises the wiring
    rather than the function: it would catch a regression that reintroduced the
    window somewhere other than _execute.
    """
    run_id = await _start(client, ["agents"])
    for _ in range(400):
        body = (await client.get(f"/api/diagnostics/runs/{run_id}")).json()
        if body["status"] in ("done", "failed"):
            stored = await gateway.storage.get_diagnostics_run(run_id)
            assert stored is not None, "terminal over the API, absent from storage"
            assert stored["status"] == body["status"]
            assert stored["ended_at"] is not None
            return
        await asyncio.sleep(0)
    raise AssertionError(f"run {run_id} never reached a terminal state")


# ---------------------------------------------------------------------------
# One ordering, whether or not storage has caught up
# ---------------------------------------------------------------------------


async def test_the_history_order_does_not_depend_on_storage(client, gateway) -> None:
    """Two branches of _history used two different orderings.

    With storage empty it returned insertion order; with storage populated it
    sorted by created_at. Both were documented as "newest first", so the same
    endpoint meant two things depending on whether a write had landed, and the
    rehydrated history could disagree with the live one about the order of the
    same runs.
    """
    await _run_to_completion(client, ["agents"])
    await _run_to_completion(client, ["sfu"])

    merged = [r["run_id"] for r in (await client.get("/api/diagnostics/runs")).json()]

    # The same working set with storage taken out of the picture, which is the
    # branch that used insertion order.
    from voicegateway.server.api.dashboard import diagnostics as d

    memory_only = [r.run_id for r in await d._history(None)]
    assert memory_only == merged

    _simulate_restart()
    from_storage = [
        r["run_id"] for r in (await client.get("/api/diagnostics/runs")).json()
    ]
    assert from_storage == merged


async def test_runs_created_in_the_same_instant_do_not_swap(client) -> None:
    """created_at alone is not a total order, so run_id breaks the tie.

    Without a tiebreak two runs sharing a timestamp order arbitrarily, and the
    history flips between reads for no reason a user can see.
    """
    from voicegateway.server.api.dashboard import diagnostics as d

    same = "2026-08-01T20:41:30.482092+00:00"
    for rid in ("bbb", "aaa", "ccc"):
        d._RUNS[rid] = d._Run(
            run_id=rid, checks=["agents"], config={}, status="done", created_at=same
        )
        d._ORDER.append(rid)

    once = [r.run_id for r in await d._history(None)]
    twice = [r.run_id for r in await d._history(None)]
    assert once == twice == ["ccc", "bbb", "aaa"]
