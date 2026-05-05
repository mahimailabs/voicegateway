#!/usr/bin/env python
"""scripts/record-streaming-fixtures.py: dev-only fixture recorder.

Hits real provider APIs to capture responses for fixture-based
replay testing in ``tests/test_streaming_cost_accounting.py``. The
recorded fixtures are committed to ``tests/fixtures/streaming/`` so
CI can replay them via HTTP and WebSocket mocking without ever
calling real APIs.

This script is **dev-only**. Default-deny:

- Without ``--record`` it prints a "recording disabled" notice and
  exits 0. Accidental invocation (cron, CI, autocomplete) cannot
  charge the user.
- With ``--record`` but without ``--confirm`` it prints an
  estimated dollar cost and exits 0. Forces an explicit
  acknowledgement that real money is about to be spent.
- With both flags it actually hits the provider API.

Usage:

    # Show the recording-disabled banner.
    python scripts/record-streaming-fixtures.py --provider openai \\
      --modality llm --model gpt-4o-mini --mode batch

    # Show the cost estimate without recording.
    python scripts/record-streaming-fixtures.py --record \\
      --provider openai --modality llm --model gpt-4o-mini --mode batch

    # Actually record.
    python scripts/record-streaming-fixtures.py --record --confirm \\
      --provider openai --modality llm --model gpt-4o-mini --mode batch

Output path:

    tests/fixtures/streaming/<provider>_<model>_<modality>_<mode>_<YYYY-MM-DD>.json

Phase 3 deliverable. Recorded payload validates against
``tests/fixtures/streaming/_schema.py``'s ``StreamingFixture``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "streaming"

DEFAULT_LLM_PROMPT = "Hello, how are you? Please respond in one short sentence."
DEFAULT_LLM_MAX_TOKENS = 100

# Decimal quantization step for expected_cost_usd. 8 decimal places
# matches the precision the StreamingFixture schema expects, fits a
# Decimal-as-string in JSON without scientific notation, and gives
# enough resolution for sub-cent fixture costs.
_COST_PRECISION = Decimal("0.00000001")
_RECORDED_BY = "scripts/record-streaming-fixtures.py"

# Estimated provider cost for the canonical Phase 3 fixtures, in USD.
# Computed from the v0.0.4 pricing catalog at the prompts and audio
# lengths the recorders below use. These are estimates surfaced before
# `--confirm`; actual cost is bounded by the provider's reply length.
_COST_ESTIMATES_USD: dict[tuple[str, str, str, str], Decimal] = {
    ("openai", "gpt-4o-mini", "llm", "batch"): Decimal("0.00003"),
    ("openai", "gpt-4o-mini", "llm", "stream"): Decimal("0.00004"),
    ("deepgram", "nova-3", "stt", "batch"): Decimal("0.00022"),
    ("deepgram", "nova-3", "stt", "stream"): Decimal("0.00022"),
    ("cartesia", "sonic-3", "tts", "batch"): Decimal("0.00620"),
    ("cartesia", "sonic-3", "tts", "stream"): Decimal("0.00620"),
}


def _fixture_path(provider: str, model: str, modality: str, mode: str) -> Path:
    today = date.today().isoformat()
    safe_model = model.replace("/", "_").replace(":", "_")
    filename = f"{provider}_{safe_model}_{modality}_{mode}_{today}.json"
    return FIXTURES_DIR / filename


def _save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str))


# ---------- OpenAI -------------------------------------------------------


async def _record_openai_llm(model: str, mode: str) -> dict[str, Any]:
    """Record an OpenAI LLM response and return an intermediate fixture shape.

    Returns a dict with three keys: ``request`` (the literal payload
    sent to the API), ``response_stream`` (a list of FixtureChunk-
    shaped dicts), and ``provider_reported_usage`` (the usage block
    normalized to ``input_tokens`` / ``output_tokens`` /
    ``total_tokens``). ``_run`` wraps this with metadata and the
    computed expected_cost_usd to produce a StreamingFixture-valid
    payload.
    """
    try:
        import openai
    except ImportError as exc:
        raise RuntimeError(
            "openai package required: pip install openai"
        ) from exc

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY env var required")

    client = openai.AsyncOpenAI(api_key=api_key)
    messages = [{"role": "user", "content": DEFAULT_LLM_PROMPT}]
    base_request: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": DEFAULT_LLM_MAX_TOKENS,
    }

    if mode == "batch":
        resp = await client.chat.completions.create(
            stream=False, **base_request
        )
        if resp.usage is None:
            raise RuntimeError(
                "OpenAI batch response did not include a usage block; "
                "cannot record a fixture without ground-truth usage."
            )
        return {
            "request": {**base_request, "stream": False},
            "response_stream": [
                {
                    "chunk_index": 0,
                    "received_at_ms": 0,
                    "data": resp.model_dump(),
                }
            ],
            "provider_reported_usage": {
                "input_tokens": resp.usage.prompt_tokens,
                "output_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            },
        }

    if mode == "stream":
        # `include_usage` surfaces the usage block on the final
        # chunk so the recorded fixture has ground-truth token
        # counts. Without it a stream fixture cannot be replayed
        # for the unit-counting assertion.
        chunks: list[dict[str, Any]] = []
        usage_normalized: dict[str, int] | None = None
        start = time.perf_counter()
        async for index, chunk in _aenumerate(
            await client.chat.completions.create(
                stream=True,
                stream_options={"include_usage": True},
                **base_request,
            )
        ):
            received_at_ms = int((time.perf_counter() - start) * 1000)
            chunk_dump = chunk.model_dump()
            chunks.append(
                {
                    "chunk_index": index,
                    "received_at_ms": received_at_ms,
                    "data": chunk_dump,
                }
            )
            if chunk.usage is not None:
                usage_normalized = {
                    "input_tokens": chunk.usage.prompt_tokens,
                    "output_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }

        if usage_normalized is None:
            raise RuntimeError(
                "OpenAI streaming response did not carry a usage "
                "block on any chunk. Was stream_options.include_usage "
                "honored by the endpoint?"
            )
        if not chunks:
            raise RuntimeError(
                "OpenAI streaming response yielded zero chunks; "
                "cannot record a stream fixture."
            )
        return {
            "request": {**base_request, "stream": True},
            "response_stream": chunks,
            "provider_reported_usage": usage_normalized,
        }

    raise ValueError(f"Unknown mode: {mode!r}")


async def _aenumerate(
    aiter: AsyncIterable[Any], start: int = 0
) -> AsyncIterator[tuple[int, Any]]:
    """Async-iterator counterpart to the builtin ``enumerate``."""
    index = start
    async for item in aiter:
        yield index, item
        index += 1


# ---------- Deepgram -----------------------------------------------------


async def _record_deepgram_stt(model: str, mode: str) -> dict[str, Any]:
    """Record a Deepgram STT response. Implementation pending in 3.2."""
    raise NotImplementedError(
        "Deepgram fixture recording: follow-up iteration. The recorder needs "
        "a small audio sample (PCM/WAV) and the deepgram-sdk live or "
        "prerecorded interface; see scripts/record-streaming-fixtures.py "
        "docstring."
    )


# ---------- Cartesia -----------------------------------------------------


async def _record_cartesia_tts(model: str, mode: str) -> dict[str, Any]:
    """Record a Cartesia TTS response. Implementation pending in 3.2."""
    raise NotImplementedError(
        "Cartesia fixture recording: follow-up iteration. The recorder "
        "needs the cartesia python SDK, a voice id, and an output_format "
        "spec; see scripts/record-streaming-fixtures.py docstring."
    )


# ---------- Dispatch / CLI -----------------------------------------------


_RECORDERS: dict[
    tuple[str, str], Callable[[str, str], Awaitable[dict[str, Any]]]
] = {
    ("openai", "llm"): _record_openai_llm,
    ("deepgram", "stt"): _record_deepgram_stt,
    ("cartesia", "tts"): _record_cartesia_tts,
}


def _list_recorders() -> None:
    print("Available recorders (use --record to actually hit the API):")
    for provider, modality in sorted(_RECORDERS.keys()):
        print(f"  --provider {provider} --modality {modality}")


def _estimate_cost_usd(
    provider: str, model: str, modality: str, mode: str
) -> Decimal | None:
    """Return the recorded-fixture estimate, or None if no estimate is on file."""
    return _COST_ESTIMATES_USD.get((provider, model, modality, mode))


def _print_recording_disabled() -> None:
    print(
        "recording disabled, use --record\n"
        "\n"
        "This script hits real provider APIs and charges real money.\n"
        "Pass --record to enable. Pass --confirm in addition to --record\n"
        "to skip the cost-estimate dry-run and actually record."
    )


def _print_cost_estimate(
    provider: str, model: str, modality: str, mode: str
) -> None:
    estimate = _estimate_cost_usd(provider, model, modality, mode)
    print(f"Recording {provider}/{model} {modality}/{mode}")
    if estimate is None:
        print(
            "  Estimated cost: unknown (no entry in _COST_ESTIMATES_USD).\n"
            "  Add an entry before recording, or pass --confirm to proceed\n"
            "  without an estimate."
        )
    else:
        print(f"  Estimated cost: ~${estimate} USD")
    print(
        "\n"
        "Pass --confirm in addition to --record to actually hit the API."
    )


def _compute_expected_cost_usd(
    provider: str,
    model: str,
    modality: str,
    usage: dict[str, Any],
) -> str:
    """Calculate, quantize, and serialize ``expected_cost_usd`` for a fixture.

    Imports the catalog facade lazily so the script can run without
    the installed package on path for the gating-only branches.
    Raises ``RuntimeError`` if the model is unknown to the catalog;
    a fixture without a known cost cannot be replayed.
    """
    from voicegateway.pricing.catalog import calculate_cost

    full_model = f"{provider}/{model}"
    if modality == "llm":
        cost = calculate_cost(
            "llm",
            full_model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
        )
    elif modality == "stt":
        cost = calculate_cost(
            "stt",
            full_model,
            audio_seconds=float(usage["audio_seconds"]),
        )
    elif modality == "tts":
        cost = calculate_cost(
            "tts",
            full_model,
            character_count=int(usage["character_count"]),
        )
    else:
        raise ValueError(f"Unknown modality: {modality!r}")

    if cost is None:
        raise RuntimeError(
            f"Pricing catalog does not know about {full_model!r} "
            f"({modality}). Add an entry before recording so "
            "expected_cost_usd is meaningful."
        )
    return str(cost.quantize(_COST_PRECISION))


def _build_fixture_payload(
    provider: str,
    model: str,
    modality: str,
    mode: str,
    intermediate: dict[str, Any],
    *,
    voicegateway_version: str,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    """Wrap a recorder's intermediate output into a StreamingFixture payload."""
    if recorded_at is None:
        recorded_at = datetime.now(UTC)
    expected_cost_usd = _compute_expected_cost_usd(
        provider, model, modality, intermediate["provider_reported_usage"]
    )
    return {
        "metadata": {
            "provider": provider,
            "model": model,
            "modality": modality,
            "mode": mode,
            "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
            "recorded_by": _RECORDED_BY,
            "voicegateway_version": voicegateway_version,
        },
        "request": intermediate["request"],
        "response_stream": intermediate["response_stream"],
        "provider_reported_usage": intermediate["provider_reported_usage"],
        "expected_cost_usd": expected_cost_usd,
    }


def _validate_payload(payload: dict[str, Any]) -> None:
    """Raise if ``payload`` does not match the StreamingFixture schema.

    Lazy import so the script's gating-only branches never pull in
    pydantic. By the time we reach validation we are about to write
    a real fixture and the cost of the import is trivial.
    """
    from tests.fixtures.streaming._schema import StreamingFixture

    StreamingFixture.model_validate(payload)


async def _run(provider: str, modality: str, model: str, mode: str) -> Path:
    recorder = _RECORDERS.get((provider, modality))
    if recorder is None:
        raise SystemExit(
            f"No recorder for {provider}/{modality}. "
            f"Available: {sorted(_RECORDERS.keys())}"
        )
    print(f"Recording {provider}/{model}/{modality}/{mode}...")
    intermediate = await recorder(model, mode)
    import voicegateway

    payload = _build_fixture_payload(
        provider,
        model,
        modality,
        mode,
        intermediate,
        voicegateway_version=voicegateway.__version__,
    )
    _validate_payload(payload)
    fixture_path = _fixture_path(provider, model, modality, mode)
    _save(fixture_path, payload)
    print(f"  -> {fixture_path}")
    print(f"     expected_cost_usd = ${payload['expected_cost_usd']}")
    return fixture_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record streaming fixtures from real provider APIs."
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Required to hit the provider API. Without this flag the "
        "script prints a recording-disabled banner and exits.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required in addition to --record to actually hit the API. "
        "Without --confirm the script prints a cost estimate and exits.",
    )
    parser.add_argument(
        "--provider", choices=["openai", "deepgram", "cartesia"]
    )
    parser.add_argument("--modality", choices=["llm", "stt", "tts"])
    parser.add_argument("--model")
    parser.add_argument(
        "--mode", choices=["batch", "stream"], default="batch"
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # Default-deny: without --record, never touch a provider.
    if not args.record:
        _print_recording_disabled()
        return

    # --record requires the per-fixture identity flags so the cost
    # estimate and the fixture filename are unambiguous.
    if not (args.provider and args.modality and args.model):
        parser.error(
            "--provider, --modality, --model are required with --record"
        )

    # --record without --confirm: dry-run cost estimate, no API call.
    if not args.confirm:
        _print_cost_estimate(
            args.provider, args.model, args.modality, args.mode
        )
        return

    asyncio.run(_run(args.provider, args.modality, args.model, args.mode))


if __name__ == "__main__":
    main()
