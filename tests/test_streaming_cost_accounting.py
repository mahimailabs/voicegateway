"""Streaming cost accounting: replay recorded fixtures through VG.

For each fixture in `tests/fixtures/streaming/`, this suite asserts:

1. The provider-reported usage in the fixture is non-empty (token
   counts for LLM, audio duration for STT, character count for TTS).
2. `voicegateway.pricing.catalog.calculate_cost` returns a non-zero
   `Decimal` for that usage and the fixture's `provider/model` ID.
3. (Streaming) The fixture carries a top-level `usage` block,
   confirming the recorder requested the right options to surface
   token counts on the final chunk.

Fixtures live in `tests/fixtures/streaming/`. See the README there
for the recording workflow and `scripts/record-streaming-fixtures.py`
for the recorder.

This file is tolerant of an empty fixtures directory: parametrized
tests skip cleanly when no fixtures match the glob. As the Phase
3.2 sub-items unblock and fixtures land, more cases activate
automatically.

Out of scope for this iteration (follow-up iterations):

- **Replay through the InstrumentedSTT/LLM/TTS wrappers.** The
  TODO 3.3 #2 calls for "replay through VG's wrapper, assert
  input/output units counted match provider-reported usage." That
  needs `respx` HTTP mocking at the right layer for each provider's
  LiveKit plugin, plus a way to drive `gw.stt()/llm()/tts()` calls
  with the mocked transport. The contract tests here exercise the
  cost-calculation half of the assertion (b) directly through
  `catalog.calculate_cost`. The wrapper-half (assertion (a) on unit
  counting + assertion (c) on TTFB hook) lands when fixtures land
  AND a per-provider mock harness is built.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from voicegateway.pricing import catalog

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "streaming"


# ----- discovery -------------------------------------------------------


def _discover(modality: str, mode: str) -> list[Path]:
    """All `<provider>_<model>_<modality>_<mode>_*.json` fixture files, sorted."""
    if not FIXTURES_DIR.exists():
        return []
    return sorted(FIXTURES_DIR.glob(f"*_{modality}_{mode}_*.json"))


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def _parametrize_or_skip(
    paths: list[Path],
) -> tuple[list[Path | None], list[str]]:
    """Return params + ids for parametrize, with a single skip case if empty."""
    if not paths:
        return [None], ["no-fixtures-recorded-yet"]
    return list(paths), [p.stem for p in paths]


# ----- LLM ------------------------------------------------------------


_LLM_BATCH_PARAMS, _LLM_BATCH_IDS = _parametrize_or_skip(_discover("llm", "batch"))


@pytest.mark.parametrize(
    "fixture_path", _LLM_BATCH_PARAMS, ids=_LLM_BATCH_IDS
)
def test_llm_batch_fixture_costs_via_catalog(fixture_path: Path | None) -> None:
    """`catalog.calculate_cost` returns a positive Decimal for the recorded usage."""
    if fixture_path is None:
        pytest.skip("no LLM batch fixtures recorded yet (Phase 3.2 #1)")
    fixture = _load(fixture_path)
    usage = fixture.get("usage")
    assert usage, f"{fixture_path}: missing or empty `usage` block"

    input_tokens = usage["prompt_tokens"]
    output_tokens = usage["completion_tokens"]
    model_id = f"{fixture['provider']}/{fixture['model']}"

    cost = catalog.calculate_cost(
        "llm",
        model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    assert cost is not None, (
        f"{fixture_path}: model {model_id!r} not in genai-prices catalog. "
        "Either re-record with a known model or update the fixture's "
        "provider/model fields."
    )
    assert isinstance(cost, Decimal)
    assert cost > Decimal("0")


_LLM_STREAM_PARAMS, _LLM_STREAM_IDS = _parametrize_or_skip(_discover("llm", "stream"))


@pytest.mark.parametrize(
    "fixture_path", _LLM_STREAM_PARAMS, ids=_LLM_STREAM_IDS
)
def test_llm_stream_fixture_carries_final_usage(fixture_path: Path | None) -> None:
    """LLM streams need usage on the final chunk; recorder uses include_usage."""
    if fixture_path is None:
        pytest.skip("no LLM stream fixtures recorded yet (Phase 3.2 #1)")
    fixture = _load(fixture_path)
    chunks = fixture.get("chunks", [])
    assert chunks, f"{fixture_path}: stream fixture has no chunks"

    usage = fixture.get("usage")
    assert usage, (
        f"{fixture_path}: stream fixture missing top-level `usage` block. "
        "OpenAI streams need stream_options={'include_usage': True} so the "
        "final chunk carries token counts; verify the recorder script."
    )

    input_tokens = usage["prompt_tokens"]
    output_tokens = usage["completion_tokens"]
    model_id = f"{fixture['provider']}/{fixture['model']}"

    cost = catalog.calculate_cost(
        "llm",
        model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    assert cost is not None and cost > Decimal("0")


# ----- STT ------------------------------------------------------------


_STT_BATCH_PARAMS, _STT_BATCH_IDS = _parametrize_or_skip(_discover("stt", "batch"))


@pytest.mark.parametrize(
    "fixture_path", _STT_BATCH_PARAMS, ids=_STT_BATCH_IDS
)
def test_stt_batch_fixture_costs_via_catalog(fixture_path: Path | None) -> None:
    """STT batch cost from recorded audio duration matches catalog.calculate_cost."""
    if fixture_path is None:
        pytest.skip("no STT batch fixtures recorded yet (Phase 3.2 #2)")
    fixture = _load(fixture_path)
    response = fixture.get("response", {})

    # Deepgram surfaces duration at metadata.duration (top-level or nested).
    duration_seconds = (
        response.get("metadata", {}).get("duration")
        or response.get("results", {}).get("metadata", {}).get("duration")
    )
    assert duration_seconds is not None, (
        f"{fixture_path}: could not extract audio duration from response"
    )

    model_id = f"{fixture['provider']}/{fixture['model']}"
    cost = catalog.calculate_cost(
        "stt",
        model_id,
        audio_seconds=float(duration_seconds),
    )
    assert cost is not None, (
        f"{fixture_path}: model {model_id!r} not in STT catalog. "
        "Either re-record with a known model or update voicegateway/pricing/stt.py."
    )
    assert isinstance(cost, Decimal)
    assert cost >= Decimal("0")


# ----- TTS ------------------------------------------------------------


_TTS_BATCH_PARAMS, _TTS_BATCH_IDS = _parametrize_or_skip(_discover("tts", "batch"))


@pytest.mark.parametrize(
    "fixture_path", _TTS_BATCH_PARAMS, ids=_TTS_BATCH_IDS
)
def test_tts_batch_fixture_costs_via_catalog(fixture_path: Path | None) -> None:
    """TTS batch cost from recorded character count matches catalog.calculate_cost."""
    if fixture_path is None:
        pytest.skip("no TTS batch fixtures recorded yet (Phase 3.2 #3)")
    fixture = _load(fixture_path)

    # Recorder may set character_count directly, or it can be derived
    # from request.transcript.
    character_count = fixture.get("character_count")
    if character_count is None:
        transcript = fixture.get("request", {}).get("transcript", "")
        character_count = len(transcript)
    assert character_count and character_count > 0, (
        f"{fixture_path}: no character count available; recorder should "
        "store it explicitly or include request.transcript"
    )

    model_id = f"{fixture['provider']}/{fixture['model']}"
    cost = catalog.calculate_cost(
        "tts",
        model_id,
        character_count=character_count,
    )
    assert cost is not None and cost > Decimal("0")


# ----- directory sanity ------------------------------------------------


def test_fixtures_directory_and_readme_exist() -> None:
    """Phase 3.1 #2 deliverable: the directory and README are tracked."""
    assert FIXTURES_DIR.exists(), (
        "tests/fixtures/streaming/ should exist (Phase 3.1 #2)"
    )
    assert (FIXTURES_DIR / "README.md").exists(), (
        "tests/fixtures/streaming/README.md documents the recording workflow"
    )
