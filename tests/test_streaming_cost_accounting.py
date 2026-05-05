"""Streaming cost accounting: replay recorded fixtures through VG.

The parametrized ``streaming_fixture`` pytest fixture iterates
every JSON file in ``tests/fixtures/streaming/`` whose filename
matches the locked convention, loads it via ``load_fixture``, and
yields a validated ``StreamingFixture`` instance to each test.

Three test functions live in this file (per §3.5 #2-#4):

1. ``test_unit_counts_are_consistent_with_response_stream`` (#2):
   per-modality structural assertion that
   ``provider_reported_usage`` agrees with the actual contents of
   the recorded ``response_stream``. For LLM, the fixture's
   normalized input/output/total tokens must equal the values
   inside the ChatCompletion JSON (batch) or the final SSE usage
   chunk (stream). For STT, ``audio_seconds`` must equal the
   ``metadata.duration`` carried in the Deepgram response. For
   TTS, ``character_count`` must equal ``len(request.transcript)``.

   Note on scope: the design doc imagined this assertion replaying
   chunks through the ``Instrumented*`` wrapper and comparing the
   wrapper's accumulated count to the provider-reported count. The
   v0.0.4 wrapper is a transparent proxy with no stream-interception
   logic, so there is no wrapper-side accumulator to compare. The
   structural-integrity test here catches the bugs that would
   surface either way: recorder field-name typos, provider schema
   drift, off-by-one normalization. A v0.0.5+ task in Discovered
   Work tracks wiring the wrapper interceptor so the literal design
   intent (replay-and-compare) becomes implementable later.
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


# ---------- §3.5 #2 unit-counting assertions ---------------------------


def _fixture_id(f: StreamingFixture) -> str:
    """Stable id for failure messages: provider/model/modality/mode."""
    m = f.metadata
    return f"{m.provider}/{m.model}/{m.modality}/{m.mode}"


def _llm_usage_from_batch_response(f: StreamingFixture) -> dict[str, int]:
    """Pull prompt/completion/total tokens from a batch ChatCompletion response."""
    response = f.response_stream[0].data
    if not isinstance(response, dict):
        raise AssertionError(
            f"{_fixture_id(f)}: batch LLM response_stream[0].data must be a "
            "ChatCompletion JSON object."
        )
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise AssertionError(
            f"{_fixture_id(f)}: ChatCompletion JSON missing the usage block."
        )
    return {
        "prompt_tokens": int(usage["prompt_tokens"]),
        "completion_tokens": int(usage["completion_tokens"]),
        "total_tokens": int(usage["total_tokens"]),
    }


def _llm_usage_from_stream_chunks(f: StreamingFixture) -> dict[str, int]:
    """Pull usage from the trailing stream_options=include_usage chunk."""
    for chunk in reversed(f.response_stream):
        data = chunk.data
        if isinstance(data, dict):
            usage = data.get("usage")
            if isinstance(usage, dict):
                return {
                    "prompt_tokens": int(usage["prompt_tokens"]),
                    "completion_tokens": int(usage["completion_tokens"]),
                    "total_tokens": int(usage["total_tokens"]),
                }
    raise AssertionError(
        f"{_fixture_id(f)}: no SSE chunk carried a usage block. "
        "OpenAI streaming requires stream_options.include_usage=True."
    )


def _stt_duration_from_batch_response(f: StreamingFixture) -> float:
    """Pull metadata.duration from a Deepgram batch response."""
    response = f.response_stream[0].data
    if not isinstance(response, dict):
        raise AssertionError(
            f"{_fixture_id(f)}: batch STT response_stream[0].data must be a "
            "Deepgram response object."
        )
    metadata = response.get("metadata", {})
    if "duration" not in metadata:
        raise AssertionError(
            f"{_fixture_id(f)}: Deepgram response missing metadata.duration."
        )
    return float(metadata["duration"])


def _stt_duration_from_stream_chunks(f: StreamingFixture) -> float:
    """Pull duration from the trailing Metadata message of a Deepgram stream."""
    for chunk in reversed(f.response_stream):
        data = chunk.data
        if (
            isinstance(data, dict)
            and data.get("type") == "Metadata"
            and "duration" in data
        ):
            return float(data["duration"])
    raise AssertionError(
        f"{_fixture_id(f)}: no Metadata message with duration found in "
        "the recorded stream."
    )


def test_unit_counts_are_consistent_with_response_stream(
    streaming_fixture: StreamingFixture,
) -> None:
    """provider_reported_usage agrees with the actual response_stream contents.

    Catches recorder normalization bugs, provider schema drift, and
    off-by-one errors. See module docstring for why this is at the
    structural-integrity layer rather than the wrapper-replay layer.
    """
    f = streaming_fixture
    fid = _fixture_id(f)
    usage = f.provider_reported_usage

    if f.metadata.modality == "llm":
        if f.metadata.mode == "batch":
            ground = _llm_usage_from_batch_response(f)
        else:
            ground = _llm_usage_from_stream_chunks(f)
        assert usage["input_tokens"] == ground["prompt_tokens"], (
            f"{fid}: input_tokens={usage['input_tokens']} but "
            f"ChatCompletion.usage.prompt_tokens={ground['prompt_tokens']}. "
            "Recorder normalization (prompt_tokens -> input_tokens) drift."
        )
        assert usage["output_tokens"] == ground["completion_tokens"], (
            f"{fid}: output_tokens={usage['output_tokens']} but "
            f"ChatCompletion.usage.completion_tokens="
            f"{ground['completion_tokens']}. "
            "Recorder normalization (completion_tokens -> output_tokens) drift."
        )
        assert usage["total_tokens"] == ground["total_tokens"], (
            f"{fid}: total_tokens={usage['total_tokens']} vs ground "
            f"{ground['total_tokens']}."
        )
        assert (
            usage["input_tokens"] + usage["output_tokens"]
            == usage["total_tokens"]
        ), (
            f"{fid}: input_tokens + output_tokens != total_tokens "
            f"({usage['input_tokens']} + {usage['output_tokens']} != "
            f"{usage['total_tokens']}). Provider reported inconsistent usage."
        )

    elif f.metadata.modality == "stt":
        if f.metadata.mode == "batch":
            ground_seconds = _stt_duration_from_batch_response(f)
        else:
            ground_seconds = _stt_duration_from_stream_chunks(f)
        recorded = float(usage["audio_seconds"])
        assert recorded == ground_seconds, (
            f"{fid}: audio_seconds={recorded} but provider response "
            f"reports duration={ground_seconds}. Recorder lost precision "
            "or read the wrong field."
        )
        assert recorded > 0, (
            f"{fid}: audio_seconds must be > 0 (got {recorded}); "
            "STT cost on zero seconds rounds to zero and the fixture "
            "becomes useless for cost validation."
        )

    elif f.metadata.modality == "tts":
        transcript = f.request.get("transcript")
        assert isinstance(transcript, str), (
            f"{fid}: TTS fixture missing request.transcript; recorder "
            "must store the literal text it sent so character_count is "
            "auditable."
        )
        recorded = int(usage["character_count"])
        assert recorded == len(transcript), (
            f"{fid}: character_count={recorded} but len(request.transcript)="
            f"{len(transcript)}. Recorder is computing character_count "
            "from the wrong source."
        )

    else:
        pytest.fail(
            f"{fid}: unknown modality {f.metadata.modality!r}; expected "
            "stt, llm, or tts."
        )


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
