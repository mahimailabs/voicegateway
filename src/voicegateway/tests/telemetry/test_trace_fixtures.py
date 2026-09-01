"""Versioned contract fixtures cover the Wave 0 trace topologies."""

from __future__ import annotations

import json
from pathlib import Path

from voicegateway.telemetry.trace_schema import SpanRecord

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "telemetry"
    / "trace_contract_scenarios.json"
)


def test_trace_contract_scenarios_are_versioned_and_valid():
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    assert payload["fixture_version"] == 1
    scenarios = payload["scenarios"]
    assert {
        "success",
        "retry",
        "fallback",
        "empty_completion",
        "schema_failure",
        "tool_refusal",
        "acknowledged_then_verified",
        "asynchronous_work",
        "late_replay",
        "cross_tenant_rejection",
    } == {scenario["name"] for scenario in scenarios}

    for scenario in scenarios:
        for span in scenario["spans"]:
            SpanRecord.model_validate(span)


def test_retry_and_fallback_fixtures_are_child_attempt_spans():
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    by_name = {scenario["name"]: scenario["spans"] for scenario in payload["scenarios"]}

    for scenario_name in ("retry", "fallback"):
        parent, attempt = by_name[scenario_name]
        assert attempt["parent_span_id"] == parent["context"]["span_id"]
        assert attempt["context"]["trace_id"] == parent["context"]["trace_id"]


def test_async_fixture_uses_a_link_instead_of_a_second_parent():
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    scenario = next(
        item for item in payload["scenarios"] if item["name"] == "asynchronous_work"
    )
    span = scenario["spans"][0]

    assert span["parent_span_id"] is None
    assert len(span["links"]) == 1
