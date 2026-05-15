"""Streaming cost accounting: replay recorded fixtures through VG."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from voicegateway.inference.pricing.catalog import calculate_cost
from voicegateway.middleware.instrumented_provider_middleware import (
    InstrumentedLLM,
    InstrumentedSTT,
    InstrumentedTTS,
    _InstrumentedBase,
)
from voicegateway.tests.fixtures.streaming._loader import (
    FIXTURES_DIR,
    discover_fixtures,
)
from voicegateway.tests.fixtures.streaming._schema import StreamingFixture

_COST_PRECISION = Decimal("0.00000001")
_WRAPPER_CLASSES: dict[str, type[_InstrumentedBase]] = {
    "stt": InstrumentedSTT,
    "llm": InstrumentedLLM,
    "tts": InstrumentedTTS,
}


_PLACEHOLDER_PATH = FIXTURES_DIR / "PLACEHOLDER.md"

_FIXTURES_PENDING_MESSAGE = (
    "Phase 3 fixtures are not yet recorded. "
    "tests/fixtures/streaming/PLACEHOLDER.md is present, marking the "
    "documented 'fixtures pending' state. Skipping the replay-driven "
    "assertions for now. Run "
    "tests/fixtures/streaming/record_streaming_fixtures.py --record --all --confirm to "
    "record the six minimum fixtures, then delete PLACEHOLDER.md in "
    "the same commit (see PLACEHOLDER.md for the runbook). The replay "
    "tests activate automatically on the next CI run."
)

_INCONSISTENT_STATE_MESSAGE = (
    "Inconsistent fixtures-directory state: "
    "tests/fixtures/streaming/PLACEHOLDER.md is gone (which signals "
    "fixtures should be present), but discover_fixtures() returned "
    "zero fixture JSONs. Either restore PLACEHOLDER.md (the suite "
    "will skip with a clear 'fixtures pending' note) or commit the "
    "fixture JSONs the recorder produced. Phase 3 acceptance "
    "criterion #1 is unmet."
)


def _placeholder_marker_present(fixtures_dir: Path | None = None) -> bool:
    """Returns True when ``PLACEHOLDER.md`` exists in ``fixtures_dir``."""
    base = fixtures_dir if fixtures_dir is not None else FIXTURES_DIR
    return (base / "PLACEHOLDER.md").exists()


def _streaming_fixture_params(
    fixtures_dir: Path | None = None,
) -> list[Any]:
    """Build the parametrize list."""
    base = fixtures_dir if fixtures_dir is not None else FIXTURES_DIR
    fixtures = discover_fixtures(base)
    if fixtures:
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
    if _placeholder_marker_present(base):
        return [
            pytest.param(
                None,
                id="fixtures-pending-PLACEHOLDER-present",
                marks=pytest.mark.skip(reason=_FIXTURES_PENDING_MESSAGE),
            )
        ]
    return [
        pytest.param(None, id="inconsistent-fixtures-state-FAIL"),
    ]


def _ensure_fixture_or_fail(
    f: StreamingFixture | None,
) -> StreamingFixture:
    """Return ``f`` or fail loudly with the inconsistent-state message."""
    if f is None:
        pytest.fail(_INCONSISTENT_STATE_MESSAGE)
    return f


@pytest.fixture(params=_streaming_fixture_params())
def streaming_fixture(request: pytest.FixtureRequest) -> StreamingFixture:
    """Yield each discovered ``StreamingFixture`` to the test using it."""
    return _ensure_fixture_or_fail(request.param)


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
    """provider_reported_usage agrees with the actual response_stream contents."""
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
            usage["input_tokens"] + usage["output_tokens"] == usage["total_tokens"]
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


# ---------- §3.5 #3 cost-calculation assertion -------------------------


def _calculate_cost_from_usage(f: StreamingFixture) -> Decimal:
    """Run provider_reported_usage through the catalog facade for f's modality."""
    full_model = f"{f.metadata.provider}/{f.metadata.model}"
    usage = f.provider_reported_usage
    if f.metadata.modality == "llm":
        cost = calculate_cost(
            "llm",
            full_model,
            input_tokens=int(usage["input_tokens"]),
            output_tokens=int(usage["output_tokens"]),
        )
    elif f.metadata.modality == "stt":
        cost = calculate_cost(
            "stt",
            full_model,
            audio_seconds=float(usage["audio_seconds"]),
        )
    elif f.metadata.modality == "tts":
        cost = calculate_cost(
            "tts",
            full_model,
            character_count=int(usage["character_count"]),
        )
    else:
        raise AssertionError(
            f"{_fixture_id(f)}: unknown modality {f.metadata.modality!r}"
        )
    if cost is None:
        raise AssertionError(
            f"{_fixture_id(f)}: pricing catalog does not know about "
            f"{full_model!r}. Either re-record with a known model or "
            "update the catalog. expected_cost_usd in the fixture was "
            f"{f.expected_cost_usd}, but the live catalog returned None."
        )
    return cost


def test_cost_calculation_matches_expected_cost_usd(
    streaming_fixture: StreamingFixture,
) -> None:
    """calculate_cost(provider_reported_usage) == fixture.expected_cost_usd."""
    f = streaming_fixture
    fid = _fixture_id(f)

    live_cost = _calculate_cost_from_usage(f)
    quantized = live_cost.quantize(_COST_PRECISION)
    expected = f.expected_cost_usd.quantize(_COST_PRECISION)

    assert quantized == expected, (
        f"{fid}: cost-layer regression. fixture.expected_cost_usd="
        f"${expected} but calculate_cost(provider_reported_usage) now "
        f"returns ${quantized} (raw {live_cost}). The fixture pins the "
        "price at recording time; if the catalog's price changed, the "
        "right fix is to re-record this fixture, not to update the "
        "expected value."
    )


# ---------- §3.5 #4 TTFB hook assertion --------------------------------


def _build_wrapper_for_fixture(f: StreamingFixture) -> _InstrumentedBase:
    """Construct the modality-correct Instrumented wrapper with mocked deps."""
    cls = _WRAPPER_CLASSES[f.metadata.modality]
    cost_tracker = MagicMock()
    cost_tracker.create_record = MagicMock(return_value=MagicMock())
    cost_tracker.notify_spend = AsyncMock()
    return cls(
        wrapped=MagicMock(),
        model_id=f"{f.metadata.provider}/{f.metadata.model}",
        provider=f.metadata.provider,
        project="default",
        cost_tracker=cost_tracker,
        storage=None,
    )


def test_ttfb_hook_fires_on_first_chunk(
    streaming_fixture: StreamingFixture,
) -> None:
    """Behavior-level: TTFB metric is set after first chunk, not before."""
    f = streaming_fixture
    if f.metadata.mode == "batch":
        pytest.skip(
            f"{_fixture_id(f)}: batch fixtures have no streaming TTFB "
            "(by design ttfb_ms == total_latency_ms for batch)."
        )

    if not f.response_stream:
        pytest.fail(
            f"{_fixture_id(f)}: stream fixture has zero chunks; "
            "cannot exercise TTFB hook."
        )

    # Behavior 1: before any chunk arrives, the hook has not fired.
    # Observable via _log_request producing ttfb_ms == total_latency_ms.
    pre_wrapper = _build_wrapper_for_fixture(f)
    pre_cost_tracker = object.__getattribute__(pre_wrapper, "_cost_tracker")
    asyncio.run(pre_wrapper._log_request(input_units=1.0))
    pre_kwargs = pre_cost_tracker.create_record.call_args.kwargs
    assert pre_kwargs["ttfb_ms"] == pre_kwargs["total_latency_ms"], (
        f"{_fixture_id(f)}: when the TTFB hook never fires, ttfb_ms "
        "must fall back to total_latency_ms. Diverging means the "
        "wrapper introduced a different fallback path."
    )

    # Behavior 2: the hook fires when the first chunk arrives. Drive
    # this with a small real-time delay before _mark_first_byte() and
    # another delay after, so total_latency_ms is measurably larger
    # than ttfb_ms.
    #
    # Construct the wrapper here (not before Behavior 1) so its
    # _start_time captures only the intended timing window. If we
    # built it earlier, total_latency_ms would include the time spent
    # in Behavior 1, blurring the assertion's meaning.
    wrapper = _build_wrapper_for_fixture(f)

    async def _drive() -> None:
        await asyncio.sleep(0.005)
        wrapper._mark_first_byte()
        # The wrapper saw the first content chunk; subsequent chunks
        # in the recorded stream do not change TTFB (idempotent).
        for _ in f.response_stream[1:]:
            wrapper._mark_first_byte()
        await asyncio.sleep(0.020)
        await wrapper._log_request(input_units=1.0)

    asyncio.run(_drive())

    cost_tracker = object.__getattribute__(wrapper, "_cost_tracker")
    create_record_kwargs = cost_tracker.create_record.call_args.kwargs
    ttfb_ms = create_record_kwargs["ttfb_ms"]
    total_ms = create_record_kwargs["total_latency_ms"]

    assert ttfb_ms > 0, (
        f"{_fixture_id(f)}: ttfb_ms must be positive after the hook "
        f"fires, got {ttfb_ms}."
    )
    assert ttfb_ms < total_ms, (
        f"{_fixture_id(f)}: ttfb_ms ({ttfb_ms:.2f}) must be less than "
        f"total_latency_ms ({total_ms:.2f}) when _mark_first_byte was "
        "called partway through. Equal values mean the hook recorded "
        "the end time instead of the first-chunk time, OR the wrapper "
        "is not consulting _first_byte_time in _log_request."
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
    repo_root = Path(__file__).resolve().parent.parent.parent
    recorder = (
        repo_root / "tests" / "fixtures" / "streaming" / "record_streaming_fixtures.py"
    )
    assert recorder.exists(), (
        f"tests/fixtures/streaming/record_streaming_fixtures.py expected at {recorder}"
    )


# ---------- fixture-state contract -------------------------------------


def test_streaming_fixture_params_skips_when_placeholder_present(
    tmp_path: Path,
) -> None:
    """Empty dir + PLACEHOLDER.md present -> single skip-marked param."""
    (tmp_path / "PLACEHOLDER.md").write_text("# pending\n", encoding="utf-8")

    params = _streaming_fixture_params(tmp_path)
    assert len(params) == 1
    p = params[0]
    assert p.values == (None,)
    assert "pending" in p.id.lower(), (
        f"sentinel id should name the pending state, got {p.id!r}"
    )
    assert any("skip" in str(m).lower() for m in p.marks), (
        f"pending sentinel must carry a skip marker; got marks={p.marks!r}"
    )


def test_streaming_fixture_params_fails_closed_without_placeholder(
    tmp_path: Path,
) -> None:
    """Empty dir + PLACEHOLDER.md absent -> fail-closed sentinel."""
    # tmp_path has no PLACEHOLDER.md and no fixtures.
    params = _streaming_fixture_params(tmp_path)
    assert len(params) == 1
    p = params[0]
    assert p.values == (None,)
    assert "inconsistent" in p.id.lower() and "fail" in p.id.lower(), (
        f"fail-closed sentinel id should name the failure, got {p.id!r}"
    )
    # No skip marker on the fail-closed path.
    assert all("skip" not in str(m).lower() for m in p.marks), (
        f"fail-closed sentinel must not carry a skip marker; got marks={p.marks!r}"
    )


def test_ensure_fixture_or_fail_raises_when_param_is_none() -> None:
    """The fixture body fails loudly with the actionable message."""
    with pytest.raises(BaseException, match="Inconsistent") as exc:
        _ensure_fixture_or_fail(None)
    msg = str(exc.value)
    assert "PLACEHOLDER.md" in msg
    assert "criterion #1" in msg


def test_ensure_fixture_or_fail_passes_through_real_fixture() -> None:
    """Non-None params pass straight through; only the sentinel fails."""
    from unittest.mock import MagicMock

    fake = MagicMock(spec=StreamingFixture)
    assert _ensure_fixture_or_fail(fake) is fake


def test_placeholder_marker_present_helper(tmp_path: Path) -> None:
    """Helper returns True/False based on PLACEHOLDER.md presence."""
    assert _placeholder_marker_present(tmp_path) is False
    (tmp_path / "PLACEHOLDER.md").write_text("# x\n", encoding="utf-8")
    assert _placeholder_marker_present(tmp_path) is True


def test_replay_suite_state_consistent() -> None:
    """Repo-level invariant: PLACEHOLDER.md gone => fixtures present."""
    placeholder = FIXTURES_DIR / "PLACEHOLDER.md"
    if placeholder.exists():
        # Documented pending state. Marker present is always OK.
        return
    fixtures = discover_fixtures(FIXTURES_DIR)
    assert fixtures, (
        "tests/fixtures/streaming/PLACEHOLDER.md is gone, which signals "
        "fixtures should be present, but discover_fixtures() returned "
        "zero. Either restore PLACEHOLDER.md or commit the fixture "
        "JSONs the recorder produced."
    )


def test_streaming_fixture_params_returns_one_per_real_fixture(
    tmp_path: Path,
) -> None:
    """When fixtures are present, params has one entry per fixture."""
    import json

    payload = {
        "metadata": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "modality": "llm",
            "mode": "batch",
            "recorded_at": "2026-05-04T14:32:11Z",
            "recorded_by": "tests/fixtures/streaming/record_streaming_fixtures.py",
            "voicegateway_version": "0.0.3",
        },
        "request": {"prompt": "hi", "stream": False},
        "response_stream": [{"chunk_index": 0, "received_at_ms": 0, "data": {"x": 1}}],
        "provider_reported_usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
        },
        "expected_cost_usd": "0.00000050",
    }
    fixture_path = tmp_path / "openai_gpt-4o-mini_llm_batch_2026-05-05.json"
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")

    params = _streaming_fixture_params(tmp_path)
    assert len(params) == 1
    p = params[0]
    assert p.values != (None,)
    assert p.id == "openai_gpt-4o-mini_llm_batch"
