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
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "streaming"

DEFAULT_LLM_PROMPT = "Say 'hello world' and nothing else."
DEFAULT_LLM_MAX_TOKENS = 20

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
    """Record an OpenAI LLM response for the canonical test prompt."""
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
        resp = await client.chat.completions.create(stream=False, **base_request)
        return {
            "request": {**base_request, "stream": False},
            "response": resp.model_dump(),
            "usage": resp.usage.model_dump() if resp.usage else None,
        }

    if mode == "stream":
        chunks: list[dict[str, Any]] = []
        usage: dict[str, Any] | None = None
        # `include_usage` is the OpenAI option that surfaces token
        # counts on the final chunk of a streamed completion. Without
        # it a stream fixture would lack the ground-truth usage we
        # rely on for VG's wrapper-counts-correctly assertions.
        async for chunk in await client.chat.completions.create(
            stream=True,
            stream_options={"include_usage": True},
            **base_request,
        ):
            chunk_dict = chunk.model_dump()
            chunks.append(chunk_dict)
            if chunk.usage:
                usage = chunk.usage.model_dump()
        return {
            "request": {**base_request, "stream": True},
            "chunks": chunks,
            "usage": usage,
        }

    raise ValueError(f"Unknown mode: {mode!r}")


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


async def _run(provider: str, modality: str, model: str, mode: str) -> Path:
    recorder = _RECORDERS.get((provider, modality))
    if recorder is None:
        raise SystemExit(
            f"No recorder for {provider}/{modality}. "
            f"Available: {sorted(_RECORDERS.keys())}"
        )
    print(f"Recording {provider}/{model}/{modality}/{mode}...")
    payload = await recorder(model, mode)
    payload["recorded_at"] = datetime.now(UTC).isoformat()
    payload["provider"] = provider
    payload["model"] = model
    payload["modality"] = modality
    payload["mode"] = mode
    fixture_path = _fixture_path(provider, model, modality, mode)
    _save(fixture_path, payload)
    print(f"  -> {fixture_path}")
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
