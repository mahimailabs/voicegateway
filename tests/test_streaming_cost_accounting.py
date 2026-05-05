"""Streaming cost accounting: replay recorded fixtures through VG.

Skeleton for the Phase 3 replay test suite. The parametrized
``streaming_fixture`` pytest fixture iterates every JSON file in
``tests/fixtures/streaming/`` whose filename matches the locked
convention, loads it via ``load_fixture``, and yields a validated
``StreamingFixture`` instance to each test.

Three test functions live in this file (added in §3.5 #2-#4):

1. ``test_unit_counting_matches_provider_reported_usage`` (#2):
   feeds the recorded ``response_stream`` chunks through the
   appropriate ``InstrumentedSTT/LLM/TTS`` wrapper and asserts the
   wrapper's accumulated unit count matches
   ``provider_reported_usage``.
2. ``test_cost_calculation_matches_expected_cost_usd`` (#3):
   passes ``provider_reported_usage`` through
   ``voicegateway.pricing.catalog.calculate_cost`` and asserts the
   quantized result equals the fixture's ``expected_cost_usd``.
3. ``test_ttfb_hook_fires_on_first_chunk`` (#4): replays the
   stream and asserts the wrapper's TTFB hook fires when the first
   content chunk arrives, not at request issuance.

When no fixtures are committed, the parametrize emits a single
skipped case with a clear "fixtures not recorded yet" reason so
the suite stays green pre-recording. As soon as
``scripts/record-streaming-fixtures.py --record --all --confirm``
lands six fixtures in the directory, those skipped cases activate
automatically; nothing in this file changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.streaming._loader import (
    FIXTURES_DIR,
    discover_fixtures,
)
from tests.fixtures.streaming._schema import StreamingFixture


def _streaming_fixture_params() -> list[Any]:
    """Parametrize one case per discovered fixture, or a single skip case."""
    fixtures = discover_fixtures(FIXTURES_DIR)
    if not fixtures:
        return [
            pytest.param(
                None,
                id="no-fixtures-recorded-yet",
                marks=pytest.mark.skip(
                    reason=(
                        "no fixtures committed under "
                        "tests/fixtures/streaming/. Run "
                        "scripts/record-streaming-fixtures.py --record "
                        "--all --confirm to populate them. See "
                        "tests/fixtures/streaming/PLACEHOLDER.md for "
                        "the full runbook."
                    )
                ),
            )
        ]
    return [
        pytest.param(
            f,
            id=(
                f"{f.metadata.provider}_{f.metadata.model}_"
                f"{f.metadata.modality}_{f.metadata.mode}"
            ),
        )
        for f in fixtures
    ]


@pytest.fixture(params=_streaming_fixture_params())
def streaming_fixture(request: pytest.FixtureRequest) -> StreamingFixture:
    """Yield each discovered ``StreamingFixture`` to the test using it."""
    return request.param  # type: ignore[no-any-return]


# ---------- skeleton smoke ---------------------------------------------


def test_streaming_fixture_skeleton_loads(
    streaming_fixture: StreamingFixture,
) -> None:
    """Sanity: parametrize wires correctly and load_fixture validated each file."""
    assert streaming_fixture.metadata.provider
    assert streaming_fixture.metadata.model
    assert streaming_fixture.metadata.modality in {"stt", "llm", "tts"}
    assert streaming_fixture.metadata.mode in {"batch", "stream"}
    assert streaming_fixture.expected_cost_usd >= 0


# ---------- repo-state guards ------------------------------------------


def test_fixtures_directory_and_readme_exist() -> None:
    """Phase 3.2 #2 deliverable: the directory and README are tracked."""
    assert FIXTURES_DIR.exists(), (
        "tests/fixtures/streaming/ should exist (Phase 3.2 #1)"
    )
    assert (FIXTURES_DIR / "README.md").exists(), (
        "tests/fixtures/streaming/README.md documents the recording workflow"
    )


def test_recording_script_exists() -> None:
    """Phase 3.3 deliverable: the recording script is at the documented path."""
    repo_root = Path(__file__).resolve().parent.parent
    recorder = repo_root / "scripts" / "record-streaming-fixtures.py"
    assert recorder.exists(), (
        f"scripts/record-streaming-fixtures.py expected at {recorder}"
    )
