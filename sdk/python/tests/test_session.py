"""attach() on a real AgentSession driven by LiveKit's FakeLLM, against a local endpoint."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from conftest import CONTRACT, Endpoint
from fake_llm import FakeLLM, FakeLLMResponse
from jsonschema import Draft202012Validator
from livekit.agents import Agent, AgentSession, function_tool
from livekit.agents.llm import FunctionToolCall, LLMError
from livekit.agents.testing import fake_job_context
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    ErrorEvent,
    UserStateChangedEvent,
)
from livekit.agents.voice.run_result import RunResult

import voicegateway

FULL_CALL = json.loads((CONTRACT / "examples" / "valid" / "full-call.json").read_text())


class Bakery(Agent):
    def __init__(self) -> None:
        super().__init__(instructions="You answer for a small bakery.")

    @function_tool
    async def lookup_hours(self) -> str:
        """Look up tomorrow's opening hours."""
        return "09:00"


def fake_llm() -> FakeLLM:
    return FakeLLM(
        fake_responses=[
            FakeLLMResponse(
                input="What time do you open tomorrow?",
                content="",
                ttft=0.01,
                duration=0.02,
                tool_calls=[
                    FunctionToolCall(name="lookup_hours", arguments="{}", call_id="call_1")
                ],
            ),
            FakeLLMResponse(
                input="09:00", content="We open at nine tomorrow.", ttft=0.01, duration=0.02
            ),
        ]
    )


async def run_call(url: str, **attach: Any) -> voicegateway.Attachment:
    with fake_job_context(job_id="AJ_test0001"):
        llm = fake_llm()
        session: AgentSession[None] = AgentSession(llm=llm)
        attachment = voicegateway.attach(session, url=url, **attach)
        await session.start(Bakery())
        # A recoverable model error, as LiveKit reports one.
        err = LLMError(
            timestamp=time.time(),
            label="fake",
            error=ConnectionError("connection reset, retrying"),
            recoverable=True,
        )
        session.emit("error", ErrorEvent(error=err, source=llm))
        # Text mode has no audio, so no user state: the agent emits these on a call.
        speaking = UserStateChangedEvent(old_state="listening", new_state="speaking")
        session.emit("user_state_changed", speaking)
        listening = UserStateChangedEvent(old_state="speaking", new_state="listening")
        session.emit("user_state_changed", listening)
        result: RunResult[None] = await session.run(user_input="What time do you open tomorrow?")
        result.expect.next_event().is_function_call(name="lookup_hours")
        result.expect.next_event().is_function_call_output(output="09:00")
        result.expect.next_event().is_message(role="assistant")
        result.expect.no_more_events()
        attachment.set_field("plan", "trial")
        await session.aclose()
        await attachment.close()
    return attachment


def shape(event: dict[str, Any]) -> tuple[str, frozenset[str]]:
    return event["type"], frozenset(event["data"])


async def test_session_events_match_the_contract(
    creds: None, endpoint: Endpoint, validator: Draft202012Validator
) -> None:
    attachment = await run_call(endpoint.url, fields={"customer": "acme", "Bad Key": "x"})

    assert endpoint.batches, "no batch arrived"
    for batch in endpoint.batches:
        validator.validate(batch)
        assert batch["sdk"] == {"name": "voicegateway-python", "version": voicegateway.__version__}
        assert batch["framework"]["name"] == "livekit-agents"
        assert batch["call"]["jobId"] == "AJ_test0001"
        assert batch["call"]["roomSid"].startswith("RM_")
    assert len({b["batchId"] for b in endpoint.batches}) == len(endpoint.batches)

    events = endpoint.events()
    types = [e["type"] for e in events]
    for wanted in ("fields", "state", "message", "usage", "tool", "error", "close"):
        assert wanted in types, f"no {wanted} event in {types}"
    assert types[-1] == "close"

    # Every event the SDK sent has the same data keys as the fixture event of its type.
    fixture_shapes = {shape(e) for e in FULL_CALL["events"]}
    for event in events:
        assert shape(event) in fixture_shapes, event

    by_type: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_type.setdefault(event["type"], []).append(event["data"])
    assert {"fields": {"customer": "acme"}} in by_type["fields"]
    assert {"fields": {"plan": "trial"}} in by_type["fields"]
    assert {"actor": "user", "state": "speaking"} in by_type["state"]
    assert any(d["actor"] == "agent" for d in by_type["state"])
    user, assistant = by_type["message"]
    assert {k: user[k] for k in ("role", "text", "interrupted")} == {
        "role": "user",
        "text": "What time do you open tomorrow?",
        "interrupted": False,
    }
    assert {k: assistant[k] for k in ("role", "text", "interrupted")} == {
        "role": "assistant",
        "text": "We open at nine tomorrow.",
        "interrupted": False,
    }
    # ChatMessage.metrics passed through: LiveKit's per-message report, or null.
    assert user["metrics"] is None or isinstance(user["metrics"], dict)
    assert isinstance(assistant["metrics"], dict) and "llm_node_ttft" in assistant["metrics"]
    assert {"name": "lookup_hours", "error": None} in by_type["tool"]
    assert by_type["error"] == [
        {"source": "FakeLLM", "message": "connection reset, retrying", "recoverable": True}
    ]
    # session_usage_updated: cumulative per-model usage, each model keeping its type.
    latest = by_type["usage"][-1]["models"]
    assert [m["type"] for m in latest] == ["llm_usage"]
    assert latest[0]["model"] and latest[0]["output_tokens"] >= 0
    assert by_type["close"] == [{"reason": by_type["close"][0]["reason"]}]
    assert by_type["close"][0]["reason"] is not None

    assert attachment.dropped == 0
    assert attachment.dropped_fields == 1


async def test_endpoint_refusing_connections_drops_and_counts(creds: None, closed_url: str) -> None:
    attachment = await run_call(closed_url)
    assert attachment.dropped > 0


async def test_endpoint_answering_500_drops_and_counts(creds: None, endpoint: Endpoint) -> None:
    endpoint.statuses = [500] * 1000
    attachment = await run_call(endpoint.url)
    assert endpoint.attempts > 0
    assert endpoint.batches == []
    assert attachment.dropped > 0


async def test_enqueue_costs_under_a_millisecond(creds: None, endpoint: Endpoint) -> None:
    with fake_job_context(job_id="AJ_bench"):
        session: AgentSession[None] = AgentSession(llm=fake_llm())
        attachment = voicegateway.attach(session, url=endpoint.url)
        ev = AgentStateChangedEvent(old_state="listening", new_state="thinking")
        n = 10_000
        start = time.perf_counter()
        for _ in range(n):
            # The whole agent-side path: LiveKit's emit, our handler, the enqueue.
            session.emit("agent_state_changed", ev)
        per_event = (time.perf_counter() - start) / n
        await attachment.close()
    assert per_event < 0.001, f"{per_event * 1e6:.1f} us per event"
    assert attachment.dropped == 0


@pytest.mark.parametrize("missing", ["VOICEGATEWAY_URL", "LIVEKIT_API_KEY"])
async def test_missing_config_fails_open(
    monkeypatch: pytest.MonkeyPatch, creds: None, missing: str, caplog: pytest.LogCaptureFixture
) -> None:
    if missing == "VOICEGATEWAY_URL":
        url = None
    else:
        url = "http://127.0.0.1:9/ingest/o/p"
        monkeypatch.delenv(missing)
    session: AgentSession[None] = AgentSession(llm=fake_llm())
    attachment = voicegateway.attach(session, url=url)
    assert not attachment.enabled
    assert missing in caplog.text
    attachment.set_field("a", "b")
    await attachment.close()
    assert attachment.dropped == 0
