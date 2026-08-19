"""The collector accepts tool-call rows, and keeps accepting newer row shapes.

Two separate guarantees, and the second is worth more than the first.

THE ROUTE. `RemoteCollectorSink` has posted to `/v1/ingest/tool-calls` since
tool-call capture shipped, and no route existed. The request fell through to the
SPA fallback (`/{full_path:path}`, GET-only), so a POST matched the path, failed
the method check, and came back 405. The sink is best-effort and requeues
quietly, so nothing surfaced: a collector deployment simply had no tool rows and
no error explaining why.

Worth recording how that was misdiagnosed, because the reasoning looked sound. A
405 on POST where GET gives 404 suggests something IS registered at the path,
which points at a misregistered route rather than a missing one. The test that
settles it is a request to a path that CANNOT exist: if that also gives 405, the
405 is a fact about the server, not about the path. It does, because of the
catch-all.

FORWARD COMPATIBILITY. An agent upgraded ahead of its collector must keep
delivering. That holds today because every ingest parser drops unknown keys, but
it held by accident rather than by contract, and the failure mode if it ever
tightens is total rather than partial: every row rejected instead of one kind.
"""

from __future__ import annotations

import uuid
import warnings

import pytest
import yaml
from fastapi.testclient import TestClient

from voicegateway.core.gateway import Gateway
from voicegateway.server.main import build_app

_CFG = {
    "providers": {},
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A collector built from an EXPLICIT config.

    Not ``Gateway()``: that resolves a voicegw.yaml from the ambient
    environment, which exists on a developer machine and does not in CI, so the
    tests passed locally and failed on all three Python versions. A fixture that
    depends on where it is run is not a fixture.
    """
    warnings.filterwarnings("ignore")
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "ingest.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    cfg = tmp_path / "voicegw.yaml"
    cfg.write_text(yaml.dump(_CFG))
    gateway = Gateway(config_path=str(cfg))
    return TestClient(build_app(gateway, enable_mcp_sse=False))


# --------------------------------------------------------------------------
# The route exists
# --------------------------------------------------------------------------


def test_the_tool_calls_route_accepts_a_post(client) -> None:
    """It answered 405 before this route existed."""
    result = client.post("/v1/ingest/tool-calls", json=[])
    assert result.status_code == 200
    assert result.json() == {"accepted": 0}


def test_a_405_alone_never_proved_the_route_existed(client) -> None:
    """The control that settles a 405, pinned so the reasoning is not lost.

    A path that cannot exist in any version answers exactly as the tool-calls
    path did before the fix. So 405-on-POST is a fact about this app having a
    GET-only catch-all, not evidence that anything is registered at the path.
    """
    bogus = "/v1/ingest/definitely-not-a-route-" + uuid.uuid4().hex
    assert client.get(bogus).status_code == 404
    assert client.post(bogus, json=[]).status_code == 405


# --------------------------------------------------------------------------
# No payload can enter through this route
# --------------------------------------------------------------------------


def test_arguments_and_results_cannot_be_stored_even_if_an_agent_sends_them(
    client,
) -> None:
    """The guarantee is enforced on the COLLECTOR side of the wire too.

    The agent is written not to send a payload, but a collector that would
    persist one if it arrived is trusting every agent that ever posts to it.
    Unknown keys are dropped, and there is no column for them.
    """
    from voicegateway.server.api.ingest import _tool_call_from_payload

    row = _tool_call_from_payload(
        {
            "session_id": "s",
            "call_id": "c1",
            "tool_name": "lookup_order",
            "started_at_ms": 1000,
            "duration_ms": 250,
            "outcome": "completed",
            "arguments": {"order_id": "SECRET"},
            "result": "customer PII",
        }
    )
    assert row is not None
    dumped = row.model_dump()
    assert not any(k in dumped for k in ("arguments", "result"))
    assert "SECRET" not in str(dumped)
    assert "PII" not in str(dumped)


def test_an_agent_supplied_primary_key_is_ignored(client) -> None:
    """Honouring it would let one agent overwrite another agent's row."""
    from voicegateway.server.api.ingest import _tool_call_from_payload

    row = _tool_call_from_payload(
        {
            "id": 999,
            "session_id": "s",
            "call_id": "c1",
            "tool_name": "t",
            "started_at_ms": 1,
        }
    )
    assert row is not None and row.id is None


# --------------------------------------------------------------------------
# An agent newer than its collector keeps delivering
# --------------------------------------------------------------------------


def _record(**extra) -> dict:
    """A cost row with a FRESH id: a reused one dedups and returns
    accepted=0, which reads exactly like a rejection and is not one."""
    base = {
        "id": str(uuid.uuid4()),
        "timestamp": 1_785_661_200.0,
        "modality": "llm",
        "model_id": "openai/gpt-4o-mini",
        "provider": "openai",
        "project": "default",
    }
    base.update(extra)
    return base


def test_a_row_carrying_an_unknown_field_is_still_accepted(client) -> None:
    """THE CONTRACT THIS FILE EXISTS FOR.

    A fleet upgrades agents and collectors at different times whether or not
    anyone intends it to. If a newer row shape were rejected, the failure would
    be total (every cost and latency row) rather than partial (one row type),
    and it would arrive at whatever moment somebody rolled an agent forward.
    """
    result = client.post(
        "/v1/ingest",
        json=[_record(a_field_from_a_later_release="whatever", another=123)],
    )
    assert result.status_code == 200
    assert result.json()["accepted"] == 1


def test_the_revision_field_specifically_survives_an_older_parser(client) -> None:
    """`revision` is the concrete case: 0.25 agents send it, 0.24 collectors
    have never heard of it, and those pairings exist in the wild right now."""
    result = client.post("/v1/ingest", json=[_record(revision="abc123")])
    assert result.status_code == 200
    assert result.json()["accepted"] == 1


def test_the_same_row_twice_dedups_rather_than_rejecting(client) -> None:
    """Guards the test above from a false green.

    A reused id returns accepted=0, which is indistinguishable from a rejection
    if you are not expecting it. Naming it here means a future reader of these
    tests cannot mistake one for the other.
    """
    row = _record()
    first = client.post("/v1/ingest", json=[row])
    second = client.post("/v1/ingest", json=[row])
    assert first.json()["accepted"] == 1
    assert second.json()["accepted"] == 0
    assert second.json().get("duplicates") == 1
