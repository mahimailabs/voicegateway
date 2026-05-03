#!/usr/bin/env python
"""scripts/record-streaming-fixtures.py: dev-only fixture recorder.

Hits real provider APIs to capture responses for fixture-based replay
testing in `tests/test_streaming_cost_accounting.py`. The recorded
fixtures are committed to `tests/fixtures/streaming/` so CI can
replay them via HTTP mocking without ever calling real APIs.

This script is **dev-only**: it requires a `--record` flag to
actually hit a provider, and reads API keys from environment
variables (see `.env.fixtures.example`). CI does not run this
script.

Usage:

    # List available recorders without hitting anything.
    python scripts/record-streaming-fixtures.py

    # Record a single fixture.
    python scripts/record-streaming-fixtures.py --record \\
      --provider openai --modality llm --model gpt-4o-mini --mode batch

Output path:

    tests/fixtures/streaming/<provider>_<model>_<modality>_<mode>_<YYYY-MM-DD>.json

Recorded payload shape (every fixture):

    {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "modality": "llm",
      "mode": "stream",
      "recorded_at": "2026-05-04T08:50:00+00:00",
      "request": {...},
      # batch:
      "response": {...},
      "usage": {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...},
      # or stream:
      "chunks": [...],
      "usage": {...}
    }

Phase 3.1 #1 deliverable. Phase 3.2 is the actual recording runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "streaming"

DEFAULT_LLM_PROMPT = "Say 'hello world' and nothing else."
DEFAULT_LLM_MAX_TOKENS = 20


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record streaming fixtures from real provider APIs."
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Required to hit the provider API. Without this flag the "
        "script lists available recorders and exits.",
    )
    parser.add_argument("--provider", choices=["openai", "deepgram", "cartesia"])
    parser.add_argument("--modality", choices=["llm", "stt", "tts"])
    parser.add_argument("--model")
    parser.add_argument("--mode", choices=["batch", "stream"], default="batch")
    args = parser.parse_args()

    if not args.record:
        _list_recorders()
        return

    if not (args.provider and args.modality and args.model):
        parser.error(
            "--provider, --modality, --model are required with --record"
        )

    asyncio.run(_run(args.provider, args.modality, args.model, args.mode))


if __name__ == "__main__":
    main()
