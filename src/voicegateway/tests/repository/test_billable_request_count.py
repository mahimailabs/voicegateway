"""A cost total and a row count are not the same population.

`requests` holds three kinds of row and only one of them can cost anything:

* billable calls, where a vendor charged us,
* error rows, where the call failed and nothing was billed,
* end-of-utterance rows, which are local timing with no vendor call at all.

The cost summary counted all three as one population, so a cost per call
divided real money by a denominator padded with rows that could never
contribute to the numerator.

This is the failure mode that bit a careful reader rather than a careless one.
A consumer read `/v1/costs` on a live collector, saw 151 rows in 1,000 with no
model and no price, and reported 15% of traffic as unattributable spend. Every
individual row was honest: 131 were end-of-utterance records, which correctly
carry no model because no vendor call happened. The aggregate was what lied,
and it took a raw-row pull to unwind.
"""

from __future__ import annotations

from pathlib import Path

from voicegateway.middleware.cost_tracker_middleware import CostTracker
from voicegateway.services.storage_service import StorageService


async def _seeded(tmp_path: Path) -> StorageService:
    """One window holding all three row kinds."""
    storage = StorageService(str(tmp_path / "counts.db"))
    await storage._ensure_initialized()
    tracker = CostTracker()

    # Two billable calls.
    for cost in (0.10, 0.20):
        rec = tracker.create_record(
            model_id="openai/gpt-4o-mini",
            modality="llm",
            provider="openai",
            project="default",
            input_units=1000.0,
            output_units=100.0,
        )
        rec.cost_usd = cost
        await storage.log_request(rec)

    # One failed call: no units, no cost, and it is not a call anyone was billed
    # for. Counting it would make the agent look cheaper per call the more it
    # broke.
    err = tracker.create_record(
        model_id="openai/gpt-4o-mini",
        modality="llm",
        provider="openai",
        project="default",
    )
    err.status = "error"
    err.error_message = "429 queue_exceeded"
    await storage.log_request(err)

    # One end-of-utterance timing row: no vendor call happened at all.
    eou = tracker.create_record(
        model_id="",
        modality="eou",
        provider="",
        project="default",
    )
    await storage.log_request(eou)
    return storage


async def test_billable_requests_excludes_errors_and_local_telemetry(
    tmp_path,
) -> None:
    """The count you divide money by."""
    storage = await _seeded(tmp_path)
    summary = await storage.get_cost_summary("all")
    await storage.aclose()
    assert summary["requests"] == 4
    assert summary["billable_requests"] == 2


async def test_the_total_is_unchanged(tmp_path) -> None:
    """Only the DENOMINATOR was ever wrong.

    Summing cost over rows that contribute zero is harmless, so `total` keeps
    its exact meaning and nobody comparing it across releases sees it move.
    """
    storage = await _seeded(tmp_path)
    summary = await storage.get_cost_summary("all")
    await storage.aclose()
    assert round(summary["total"], 4) == 0.30


async def test_cost_per_call_differs_by_a_third_between_the_two_counts(
    tmp_path,
) -> None:
    """Why this is worth a field rather than a docs note.

    Four rows against two billable ones is a 2x error on this fixture, and the
    reported real-world case was 1,000 against 849. The wrong denominator is
    always the larger one, so cost per call always reads LOW, which is the
    flattering direction and the one nobody questions.
    """
    storage = await _seeded(tmp_path)
    s = await storage.get_cost_summary("all")
    await storage.aclose()
    naive = s["total"] / s["requests"]
    honest = s["total"] / s["billable_requests"]
    assert naive < honest
    assert round(honest, 4) == 0.15
    assert round(naive, 4) == 0.075


async def test_a_window_with_no_billable_rows_reports_zero_not_a_crash(
    tmp_path,
) -> None:
    """A caller dividing by this must get a zero to guard on, not a None."""
    storage = StorageService(str(tmp_path / "empty.db"))
    await storage._ensure_initialized()
    summary = await storage.get_cost_summary("all")
    await storage.aclose()
    assert summary["requests"] == 0
    assert summary["billable_requests"] == 0
