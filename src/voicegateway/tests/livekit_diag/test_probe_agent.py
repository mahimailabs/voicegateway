"""One-agent probe: room naming, dispatch mode, and the honest-nulls contract.

``probe_agent`` places a single real call and reports what it cost. These tests
drive it with a fake LiveKit admin/client so the wiring can be checked without a
server: which room it creates, whether it issues an explicit dispatch, and what
it reports when the numbers cannot be measured. Nothing here fabricates a
latency or a cost, and neither may the code: an unmeasurable value is None.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from voicegateway.livekit_diag import latency, service
from voicegateway.repository.request_log_repository import PROBE_ROOM_PREFIX


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch):
    """Zero the pre-utterance settle so probe() does not sleep in tests."""
    monkeypatch.setattr(latency, "_AGENT_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(latency, "_REPLY_GRACE_SECONDS", 0.0)


_CREDS = SimpleNamespace(url="ws://fake", api_key="k", api_secret="s")


@pytest.fixture(autouse=True)
def _reset_fake_admin():
    """Clear the class-level handle on the last constructed admin.

    Tests assert against ``_FakeAdmin.last``, which every probe overwrites. Left
    unreset, a test whose probe never constructs one would silently assert
    against the PREVIOUS test's admin and pass on the wrong object.
    """
    _FakeAdmin.last = None
    yield
    _FakeAdmin.last = None


class _FakeAdmin:
    last: _FakeAdmin | None = None

    def __init__(self, creds):
        self.creds = creds
        self.url = creds.url
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.dispatched: list[tuple[str, str]] = []
        self.closed = False
        _FakeAdmin.last = self

    async def create_room(self, name):
        self.created.append(name)

    async def delete_room(self, name):
        self.deleted.append(name)

    async def create_dispatch(self, room, agent, metadata=""):
        self.dispatched.append((room, agent))

    def join_token(self, room, identity):
        return "tok"

    async def aclose(self):
        self.closed = True


class _FakeClient:
    def __init__(self, url, token):
        pass

    async def connect(self):
        pass

    async def publish_utterance(self, src):
        return 0.0

    async def wait_reply(self, t0, timeout=15.0):
        return 1.25

    async def disconnect(self):
        pass


class _StubUtterance:
    duration_s = 0.8

    def frames(self):
        return iter([(b"\x00\x00", 16000)])


class _FakeStore:
    """Returns fixed rows for the probe room, like an instrumented agent wrote."""

    def __init__(self, rows=None, raises=False):
        self._rows = rows or []
        self._raises = raises
        self.asked: list[str] = []

    async def get_requests_for_room(self, room, since=None):
        self.asked.append(room)
        if self._raises:
            raise RuntimeError("read-back exploded")
        return self._rows


def _rows(room):
    return [
        {
            "modality": "llm",
            "ttfb_ms": 480.0,
            "cost_usd": 0.004,
            "metadata": {"room": room},
        },
        {
            "modality": "tts",
            "ttfb_ms": 210.0,
            "cost_usd": 0.001,
            "metadata": {"room": room},
        },
    ]


def _patch(monkeypatch):
    """Swap the lazily-imported diag namespace for fakes, keeping the real
    ProbeRunner/summarize so the code under test is actually exercised."""
    from voicegateway.livekit_diag.latency import (
        ComponentReader,
        ProbeRunner,
        summarize,
    )

    real = service._diag()
    monkeypatch.setattr(
        service,
        "_diag",
        lambda: SimpleNamespace(
            LiveKitAdmin=_FakeAdmin,
            SyntheticClient=lambda url, token: _FakeClient(url, token),
            UtteranceSource=lambda path: _StubUtterance(),
            ProbeRunner=ProbeRunner,
            ComponentReader=ComponentReader,
            summarize=summarize,
            pkg=real.pkg,
        ),
    )


# ---------------------------------------------------------------------------
# Room naming
# ---------------------------------------------------------------------------


def test_probe_room_name_carries_the_exclusion_prefix() -> None:
    """The prefix is what keeps probe rows out of the agent's rollups, so it is
    asserted against the repository's constant, not a copy of the string."""
    assert service.probe_room_name("support", "ab12cd34").startswith(PROBE_ROOM_PREFIX)


def test_probe_room_name_sanitises_agent_ids() -> None:
    assert service.probe_room_name("my agent/x!", "ab12cd34") == (
        "vg-probe-my-agent-x-ab12cd34"
    )


def test_probe_room_name_survives_an_unusable_agent_id() -> None:
    assert service.probe_room_name("///", "ab12cd34") == "vg-probe-agent-ab12cd34"


def test_probe_room_name_is_unique_per_press() -> None:
    a = service.probe_room_name("support", "aaaaaaaa")
    b = service.probe_room_name("support", "bbbbbbbb")
    assert a != b


# ---------------------------------------------------------------------------
# Dispatch mode
# ---------------------------------------------------------------------------


async def test_named_agent_gets_an_explicit_dispatch(monkeypatch) -> None:
    _patch(monkeypatch)
    out = await service.probe_agent(
        _CREDS,
        agent_id="support",
        dispatch_name="support-bot",
        nonce="ab12cd34",
        warmup=False,
    )
    admin = _FakeAdmin.last
    assert admin.dispatched == [("vg-probe-support-ab12cd34", "support-bot")]
    assert out["mode"] == "explicit"
    assert out["room"] == "vg-probe-support-ab12cd34"


async def test_automatic_dispatch_creates_the_room_and_dispatches_nothing(
    monkeypatch,
) -> None:
    """An empty dispatch name means the worker joins every new room on its own.

    Issuing a dispatch for "" would target no worker, so the room creation IS
    the dispatch here.
    """
    _patch(monkeypatch)
    out = await service.probe_agent(
        _CREDS, agent_id="auto", dispatch_name="", nonce="ab12cd34", warmup=False
    )
    admin = _FakeAdmin.last
    assert admin.dispatched == []
    assert admin.created == ["vg-probe-auto-ab12cd34"]
    assert out["mode"] == "automatic"


async def test_probe_room_is_always_torn_down(monkeypatch) -> None:
    _patch(monkeypatch)
    await service.probe_agent(
        _CREDS, agent_id="a", dispatch_name="a", nonce="ab12cd34", warmup=False
    )
    admin = _FakeAdmin.last
    assert admin.deleted == ["vg-probe-a-ab12cd34"]
    assert admin.closed is True


async def test_warmup_turn_runs_in_its_own_room(monkeypatch) -> None:
    """A discarded turn must not be readable as part of the measured one.

    Warmup exists to throw away a cold start, and the e2e number does exactly
    that. Cost and the component split are read back by room, though: sharing
    one room would sum the discarded turn's spend into cost_usd and average its
    cold latencies into the split, so the card would show one press as costing
    two calls. The suffixed room keeps the vg-probe- prefix, so those rows still
    stay out of the agent's rollups.
    """
    _patch(monkeypatch)
    store = _FakeStore(_rows("vg-probe-a-ab12cd34"))
    out = await service.probe_agent(
        _CREDS,
        agent_id="a",
        dispatch_name="a",
        nonce="ab12cd34",
        warmup=True,
        store=store,
    )
    admin = _FakeAdmin.last
    assert admin.created == ["vg-probe-a-ab12cd34-warmup", "vg-probe-a-ab12cd34"]
    assert admin.deleted == admin.created  # both torn down
    assert out["trials"] == 1  # the warmup turn is not reported as a sample
    assert out["room"] == "vg-probe-a-ab12cd34"
    # Both read-backs (the component split, then the cost) asked for the
    # measured room. The warmup room is never read, so neither number can
    # include it.
    assert set(store.asked) == {"vg-probe-a-ab12cd34"}
    assert out["room"].startswith(PROBE_ROOM_PREFIX)
    assert admin.created[0].startswith(PROBE_ROOM_PREFIX)


async def test_a_probe_from_the_dashboard_places_exactly_one_call(monkeypatch) -> None:
    """The play button promises one billed call, so the endpoint asks for one.

    Guards the promise end to end: with warmup off there is a single room, a
    single dispatch, and the cost read back is that one call's, not a sum over
    a turn the operator was never told about.
    """
    _patch(monkeypatch)
    store = _FakeStore(_rows("vg-probe-a-ab12cd34"))
    out = await service.probe_agent(
        _CREDS,
        agent_id="a",
        dispatch_name="a",
        nonce="ab12cd34",
        warmup=False,
        store=store,
    )
    admin = _FakeAdmin.last
    assert admin.created == ["vg-probe-a-ab12cd34"]
    assert len(admin.dispatched) == 1
    assert round(out["cost_usd"], 6) == 0.005  # one call's rows, summed once


async def test_trials_are_capped(monkeypatch) -> None:
    _patch(monkeypatch)
    out = await service.probe_agent(
        _CREDS,
        agent_id="a",
        dispatch_name="a",
        nonce="ab12cd34",
        trials=99,
        warmup=False,
    )
    assert out["trials"] == service.MAX_PROBE_TRIALS


# ---------------------------------------------------------------------------
# What it reports
# ---------------------------------------------------------------------------


async def test_reports_measured_latency_split_and_summed_cost(monkeypatch) -> None:
    _patch(monkeypatch)
    room = "vg-probe-a-ab12cd34"
    store = _FakeStore(_rows(room))
    out = await service.probe_agent(
        _CREDS,
        agent_id="a",
        dispatch_name="a",
        nonce="ab12cd34",
        warmup=False,
        store=store,
    )
    assert out["e2e"]["avg"] == 1.25
    assert out["components"] == {"llm_ttft": 0.48, "tts": 0.21}
    assert round(out["cost_usd"], 6) == 0.005
    assert out["error"] is None


async def test_result_shape_is_pinned(monkeypatch) -> None:
    """Pin the response contract in one place.

    The endpoint passes this dict through verbatim, its own tests fake it, and
    the dashboard types it in TypeScript. Nothing else compares those three, so
    a renamed or dropped key here would surface as an undefined in the browser
    rather than a failing test. Update the AgentProbeResult interface and
    docs/api/dashboard-api.md in the same change as this assertion.
    """
    _patch(monkeypatch)
    out = await service.probe_agent(
        _CREDS,
        agent_id="a",
        dispatch_name="a",
        nonce="ab12cd34",
        warmup=False,
        store=_FakeStore(_rows("vg-probe-a-ab12cd34")),
    )
    assert set(out) == {
        "agent_id",
        "dispatch_name",
        "mode",
        "room",
        "trials",
        "e2e",
        "components",
        "cost_usd",
        "models",
        "error",
    }
    assert set(out["e2e"]) == {"avg", "p50", "p95", "min", "max", "trials"}


async def test_probe_surfaces_the_models_it_ran(monkeypatch) -> None:
    """The response carries the model per leg (for the split's hover labels), read
    from the same rows as the split and cost. None per leg the call did not run."""
    _patch(monkeypatch)
    room = "vg-probe-a-ab12cd34"
    rows = [
        {"modality": "stt", "model_id": "livekit/deepgram/nova-3",
         "metadata": {"room": room}},
        {"modality": "llm", "model_id": "livekit/google/gemma-4-31b-it",
         "metadata": {"room": room}},
        # No TTS row: that leg's model stays None, not a guess.
    ]
    out = await service.probe_agent(
        _CREDS,
        agent_id="a",
        dispatch_name="a",
        nonce="ab12cd34",
        warmup=False,
        store=_FakeStore(rows),
    )
    assert out["models"] == {
        "stt": "livekit/deepgram/nova-3",
        "llm": "livekit/google/gemma-4-31b-it",
        "tts": None,
    }


async def test_no_store_reports_null_cost_and_split_not_zero(monkeypatch) -> None:
    """An agent shipping telemetry to a remote collector wrote no rows here.

    Reporting that as $0.00 would claim the call was free, which is a different
    and false statement from "this host cannot know".
    """
    _patch(monkeypatch)
    out = await service.probe_agent(
        _CREDS, agent_id="a", dispatch_name="a", nonce="ab12cd34", warmup=False
    )
    assert out["cost_usd"] is None
    assert out["components"] is None
    assert out["e2e"]["avg"] == 1.25  # the one number the probe measured itself


async def test_store_with_no_rows_reports_null_cost(monkeypatch) -> None:
    _patch(monkeypatch)
    out = await service.probe_agent(
        _CREDS,
        agent_id="a",
        dispatch_name="a",
        nonce="ab12cd34",
        warmup=False,
        store=_FakeStore([]),
    )
    assert out["cost_usd"] is None


async def test_a_failing_read_back_does_not_fail_the_probe(monkeypatch) -> None:
    _patch(monkeypatch)
    out = await service.probe_agent(
        _CREDS,
        agent_id="a",
        dispatch_name="a",
        nonce="ab12cd34",
        warmup=False,
        store=_FakeStore(raises=True),
    )
    assert out["cost_usd"] is None
    # The same read is what would have produced the split, so it is null for the
    # same reason. A partial split surviving a failed read would render missing
    # components as 0.00, i.e. "instant".
    assert out["components"] is None
    assert out["e2e"]["avg"] == 1.25


async def test_a_failing_warmup_turn_reports_the_error_and_no_room(
    monkeypatch,
) -> None:
    """The warmup turn dying is a distinct branch: it breaks before any counted
    turn sets the room, so there is nothing to correlate cost or components to
    and both must stay null rather than fall back to the warmup's own room."""
    _patch(monkeypatch)

    class _DeadOnFirstCall:
        calls = 0

        async def wait_reply(self, t0, timeout=15.0):
            _DeadOnFirstCall.calls += 1
            raise TimeoutError("no reply on warmup")

    monkeypatch.setattr(_FakeClient, "wait_reply", _DeadOnFirstCall.wait_reply)
    store = _FakeStore(_rows("vg-probe-a-ab12cd34"))
    out = await service.probe_agent(
        _CREDS,
        agent_id="a",
        dispatch_name="a",
        nonce="ab12cd34",
        warmup=True,
        store=store,
    )
    assert out["error"] == "no reply on warmup"
    assert out["e2e"] is None
    assert out["room"] is None
    assert out["components"] is None
    assert out["cost_usd"] is None
    assert store.asked == []  # nothing to read back against
    # It bailed on the warmup rather than going on to spend a second call.
    assert _FakeAdmin.last.created == ["vg-probe-a-ab12cd34-warmup"]


async def test_a_dead_agent_reports_the_error_and_no_numbers(monkeypatch) -> None:
    _patch(monkeypatch)

    class _DeadClient(_FakeClient):
        async def wait_reply(self, t0, timeout=15.0):
            raise TimeoutError("no reply")

    monkeypatch.setattr(_FakeClient, "wait_reply", _DeadClient.wait_reply)
    out = await service.probe_agent(
        _CREDS, agent_id="a", dispatch_name="a", nonce="ab12cd34", warmup=False
    )
    assert out["error"] == "no reply"
    assert out["e2e"] is None
    assert out["cost_usd"] is None
    assert _FakeAdmin.last.deleted == ["vg-probe-a-ab12cd34"]


async def test_agent_side_error_is_surfaced_when_nothing_measured(monkeypatch) -> None:
    """The agent joined but its pipeline errored (e.g. STT 401). The synthetic
    client just sees no reply (returns None, no exception), so the probe measures
    nothing. Rather than a bland all-null, it surfaces the agent's own error rows
    so the card says WHY. Deduped: a retry storm collapses to one label."""
    _patch(monkeypatch)

    class _SilentClient(_FakeClient):
        async def wait_reply(self, t0, timeout=15.0):
            return None  # no reply, but no exception

    monkeypatch.setattr(_FakeClient, "wait_reply", _SilentClient.wait_reply)
    room = "vg-probe-a-ab12cd34"
    msg = "Invalid response status (401 Unauthorized)"
    error_rows = [
        {
            "modality": "stt",
            "status": "error",
            "error_message": msg,
            "metadata": {"room": room},
        },
        {
            "modality": "stt",
            "status": "error",
            "error_message": msg,
            "metadata": {"room": room},
        },  # duplicate retry -> deduped
    ]
    out = await service.probe_agent(
        _CREDS,
        agent_id="a",
        dispatch_name="a",
        nonce="ab12cd34",
        warmup=False,
        store=_FakeStore(error_rows),
    )
    assert out["e2e"] is None  # nothing measured
    assert out["cost_usd"] is None
    assert out["error"] == f"STT: {msg}"  # the cause, surfaced and deduped
