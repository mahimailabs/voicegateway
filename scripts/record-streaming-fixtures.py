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
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "streaming"
AUDIO_SAMPLE_PATH = REPO_ROOT / "tests" / "fixtures" / "audio" / "test_sample.wav"

DEFAULT_LLM_PROMPT = "Hello, how are you? Please respond in one short sentence."
DEFAULT_LLM_MAX_TOKENS = 100

# Canonical TTS test text. Short, neutral, no special markup. The
# fixture asserts wrapper character counts equal len(this string),
# so changing it requires re-recording the Cartesia fixtures.
DEFAULT_TTS_TEXT = "Hello, this is the canonical voicegateway phase 3 fixture."

# Cartesia request defaults. Output format is fixed at PCM 16-bit
# 16 kHz mono so the fixture's audio_size_bytes is reproducible
# across recordings; voice id falls back to an env-provided value
# so contributors can record without hard-coding their account's
# voice library.
_CARTESIA_VERSION = "2024-06-10"
_CARTESIA_OUTPUT_FORMAT = {
    "container": "raw",
    "encoding": "pcm_s16le",
    "sample_rate": 16000,
}
_CARTESIA_DEFAULT_VOICE_ID = "a0e99841-438c-4a64-b679-ae501e7d6091"

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

# The six Phase 3 fixtures, in the order --all records them.
# Order is chosen to fail fast on auth issues per provider:
# OpenAI (batch + stream), Deepgram (batch + stream), Cartesia
# (batch + stream).
_ALL_FIXTURES: list[tuple[str, str, str, str]] = [
    ("openai", "gpt-4o-mini", "llm", "batch"),
    ("openai", "gpt-4o-mini", "llm", "stream"),
    ("deepgram", "nova-3", "stt", "batch"),
    ("deepgram", "nova-3", "stt", "stream"),
    ("cartesia", "sonic-3", "tts", "batch"),
    ("cartesia", "sonic-3", "tts", "stream"),
]


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
    """Record a Deepgram STT response and return an intermediate fixture shape.

    The bundled ``tests/fixtures/audio/test_sample.wav`` (3 seconds,
    8 kHz mono 16-bit PCM, ~48 KB) is the audio source for both
    batch and stream modes. The fixture's ``request`` block stores
    a path reference rather than inlining the audio so the JSON
    stays small; replay tests load the same file when they need
    bytes.
    """
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "httpx package required: pip install httpx"
        ) from exc

    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPGRAM_API_KEY env var required")

    if not AUDIO_SAMPLE_PATH.exists():
        raise RuntimeError(
            f"Test audio sample missing at {AUDIO_SAMPLE_PATH}. "
            "Add a small WAV file under tests/fixtures/audio/."
        )

    audio_bytes = AUDIO_SAMPLE_PATH.read_bytes()
    request_record = {
        "model": model,
        "audio_path": str(AUDIO_SAMPLE_PATH.relative_to(REPO_ROOT)),
        "audio_size_bytes": len(audio_bytes),
        "audio_format": "wav",
    }

    if mode == "batch":
        # Deepgram pre-recorded API: POST raw audio bytes with the
        # model selected via query string. Response carries the
        # transcript plus a metadata block with the audio duration
        # in seconds, which is the ground-truth STT usage.
        url = f"https://api.deepgram.com/v1/listen?model={model}"
        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "audio/wav",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url, content=audio_bytes, headers=headers
            )
            resp.raise_for_status()
            response_dict = resp.json()

        duration = response_dict.get("metadata", {}).get("duration")
        if duration is None:
            raise RuntimeError(
                "Deepgram batch response missing metadata.duration; "
                "cannot record a fixture without ground-truth audio "
                "seconds."
            )
        return {
            "request": request_record,
            "response_stream": [
                {
                    "chunk_index": 0,
                    "received_at_ms": 0,
                    "data": response_dict,
                }
            ],
            "provider_reported_usage": {
                "audio_seconds": float(duration),
            },
        }

    if mode == "stream":
        # Deepgram live API: WebSocket at /v1/listen with the model
        # and PCM parameters in the query string. Audio frames are
        # raw PCM (the WAV 44-byte header is stripped). Server pushes
        # SpeechStarted / Results / UtteranceEnd JSON messages and
        # closes with a Metadata message that carries the duration
        # we use as ground truth.
        sample_rate = 8000  # matches AUDIO_SAMPLE_PATH (8 kHz mono)
        channels = 1
        url = (
            "wss://api.deepgram.com/v1/listen"
            f"?model={model}"
            f"&encoding=linear16&sample_rate={sample_rate}&channels={channels}"
        )
        # Strip the 44-byte WAV/RIFF header to get raw PCM samples.
        pcm_bytes = audio_bytes[44:]
        # 100 ms chunk = sample_rate * 2 (16-bit) * 0.1.
        chunk_size = (sample_rate * 2) // 10

        chunks: list[dict[str, Any]] = []
        usage_normalized: dict[str, float] | None = None
        cm = _open_deepgram_websocket(url, api_key)
        async with cm as ws:
            start = time.perf_counter()
            for i in range(0, len(pcm_bytes), chunk_size):
                await ws.send(pcm_bytes[i : i + chunk_size])
            # Tell Deepgram we have no more audio. The server then
            # flushes any remaining transcripts and closes with
            # Metadata.
            await ws.send(json.dumps({"type": "Finalize"}))

            index = 0
            async for message in ws:
                received_at_ms = int((time.perf_counter() - start) * 1000)
                if isinstance(message, bytes):
                    text = message.decode("utf-8")
                else:
                    text = message
                data = json.loads(text)
                chunks.append(
                    {
                        "chunk_index": index,
                        "received_at_ms": received_at_ms,
                        "data": data,
                    }
                )
                index += 1
                if data.get("type") == "Metadata":
                    duration = data.get("duration")
                    if duration is not None:
                        usage_normalized = {
                            "audio_seconds": float(duration)
                        }
                    break

            # Close the stream cleanly. Best-effort; if the server
            # already closed the socket, ignore the resulting error.
            try:
                await ws.send(json.dumps({"type": "CloseStream"}))
            except Exception:  # noqa: BLE001
                pass

        if usage_normalized is None:
            raise RuntimeError(
                "Deepgram stream did not deliver a Metadata message "
                "with duration; cannot record a fixture without "
                "ground-truth audio_seconds."
            )
        if not chunks:
            raise RuntimeError(
                "Deepgram stream yielded zero messages; cannot "
                "record an empty stream fixture."
            )
        return {
            "request": request_record,
            "response_stream": chunks,
            "provider_reported_usage": usage_normalized,
        }

    raise ValueError(f"Unknown mode: {mode!r}")


def _open_deepgram_websocket(url: str, api_key: str) -> Any:
    """Return an async context manager that opens a Deepgram WebSocket.

    Tests monkey-patch this helper so the recorder never imports
    ``websockets`` during a unit-test run. Production calls into the
    real library lazily.
    """
    try:
        from websockets.asyncio.client import connect
    except ImportError as exc:
        raise RuntimeError(
            "websockets>=13.0 required (uses websockets.asyncio.client). "
            "Install with: pip install 'websockets>=13.0'"
        ) from exc
    return connect(
        url,
        additional_headers=[("Authorization", f"Token {api_key}")],
    )


# ---------- Cartesia -----------------------------------------------------


async def _record_cartesia_tts(model: str, mode: str) -> dict[str, Any]:
    """Record a Cartesia TTS response and return an intermediate fixture shape.

    Cartesia bills TTS by *input* characters (text length) rather
    than output audio. The fixture's
    ``provider_reported_usage.character_count`` is therefore
    ``len(transcript)`` rather than something extracted from the
    HTTP response. The Cartesia HTTP API does not return a usage
    block on its own.

    Audio bytes are *not* stored in the fixture: the fixture's
    purpose is to validate VG's character-count accounting, not
    audio fidelity, and inlining ~hundreds of KB of base64 PCM
    would inflate every JSON file. Only audio metadata
    (size, format, sample rate) lands in the fixture.
    """
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "httpx package required: pip install httpx"
        ) from exc

    api_key = os.environ.get("CARTESIA_API_KEY")
    if not api_key:
        raise RuntimeError("CARTESIA_API_KEY env var required")

    voice_id = (
        os.environ.get("CARTESIA_VOICE_ID") or _CARTESIA_DEFAULT_VOICE_ID
    )
    request_payload: dict[str, Any] = {
        "model_id": model,
        "transcript": DEFAULT_TTS_TEXT,
        "voice": {"mode": "id", "id": voice_id},
        "output_format": _CARTESIA_OUTPUT_FORMAT,
    }

    if mode == "batch":
        url = "https://api.cartesia.ai/tts/bytes"
        headers = {
            "X-API-Key": api_key,
            "Cartesia-Version": _CARTESIA_VERSION,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url, json=request_payload, headers=headers
            )
            resp.raise_for_status()
            audio_bytes = resp.content

        if not audio_bytes:
            raise RuntimeError(
                "Cartesia batch response carried zero audio bytes; "
                "cannot record an empty fixture."
            )
        return {
            "request": request_payload,
            "response_stream": [
                {
                    "chunk_index": 0,
                    "received_at_ms": 0,
                    "data": {
                        "audio_size_bytes": len(audio_bytes),
                        "encoding": _CARTESIA_OUTPUT_FORMAT["encoding"],
                        "sample_rate": _CARTESIA_OUTPUT_FORMAT["sample_rate"],
                    },
                }
            ],
            "provider_reported_usage": {
                "character_count": len(DEFAULT_TTS_TEXT),
            },
        }

    if mode == "stream":
        # Cartesia live TTS API: WebSocket where the api key and
        # version live in the query string (not headers, unlike
        # Deepgram). A single JSON request is sent once at session
        # start; the server replies with a sequence of {"type":
        # "chunk", "data": <base64>, ...} messages and terminates
        # with {"type": "done"}. character_count remains
        # len(transcript) (TTS bills on input).
        url = (
            "wss://api.cartesia.ai/tts/websocket"
            f"?cartesia_version={_CARTESIA_VERSION}&api_key={api_key}"
        )
        context_id = uuid.uuid4().hex
        ws_request = {
            **request_payload,
            "context_id": context_id,
            "continue": False,
        }

        chunks: list[dict[str, Any]] = []
        cm = _open_cartesia_websocket(url)
        async with cm as ws:
            await ws.send(json.dumps(ws_request))
            start = time.perf_counter()
            index = 0
            done_seen = False
            async for message in ws:
                received_at_ms = int((time.perf_counter() - start) * 1000)
                text = (
                    message.decode("utf-8")
                    if isinstance(message, bytes)
                    else message
                )
                data = json.loads(text)
                chunks.append(
                    {
                        "chunk_index": index,
                        "received_at_ms": received_at_ms,
                        "data": data,
                    }
                )
                index += 1
                if data.get("type") == "done":
                    done_seen = True
                    break

        if not chunks:
            raise RuntimeError(
                "Cartesia stream yielded zero messages; cannot "
                "record an empty stream fixture."
            )
        if not done_seen:
            raise RuntimeError(
                "Cartesia stream did not terminate with a "
                "type=done message; cannot record an unterminated "
                "stream fixture."
            )
        return {
            "request": ws_request,
            "response_stream": chunks,
            "provider_reported_usage": {
                "character_count": len(DEFAULT_TTS_TEXT),
            },
        }

    raise ValueError(f"Unknown mode: {mode!r}")


def _open_cartesia_websocket(url: str) -> Any:
    """Return an async context manager that opens a Cartesia WebSocket.

    Tests monkey-patch this helper so the recorder never imports
    ``websockets`` during a unit-test run. Production calls into
    the real library lazily.
    """
    try:
        from websockets.asyncio.client import connect
    except ImportError as exc:
        raise RuntimeError(
            "websockets>=13.0 required (uses websockets.asyncio.client). "
            "Install with: pip install 'websockets>=13.0'"
        ) from exc
    return connect(url)


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


def _print_all_cost_estimate() -> None:
    """Print the per-fixture and aggregate cost estimate for --all."""
    print("About to record all 6 Phase 3 fixtures:")
    print()
    total = Decimal("0")
    for provider, model, modality, mode in _ALL_FIXTURES:
        estimate = _estimate_cost_usd(provider, model, modality, mode)
        if estimate is None:
            print(
                f"  {provider}/{model} {modality}/{mode}    "
                "estimate unavailable"
            )
            continue
        total += estimate
        print(
            f"  {provider}/{model:<14s} {modality:>3s}/{mode:<6s}  ~${estimate} USD"
        )
    print()
    print(f"Estimated total: ~${total} USD")
    print()
    print(
        "Pass --confirm in addition to --record --all to actually hit "
        "the APIs."
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
        "--all",
        dest="all_fixtures",
        action="store_true",
        help="Record all 6 Phase 3 fixtures sequentially with a single "
        "--confirm. Mutually exclusive with --provider/--modality/"
        "--model/--mode.",
    )
    parser.add_argument(
        "--provider", choices=["openai", "deepgram", "cartesia"]
    )
    parser.add_argument("--modality", choices=["llm", "stt", "tts"])
    parser.add_argument("--model")
    parser.add_argument(
        "--mode",
        choices=["batch", "stream"],
        default=None,
        help="batch or stream. Required with --provider/--modality/"
        "--model. Mutually exclusive with --all (which records both "
        "modes for every fixture). Defaults to 'batch' in the "
        "single-fixture path when omitted.",
    )
    return parser


async def _run_all() -> list[Path]:
    """Sequentially record every Phase 3 fixture; return the written paths.

    Prints a ``[N/total]`` progress marker before each recording so
    the human running ``--record --all --confirm`` sees real-time
    progress over the sequence rather than one big silence punctuated
    by six file paths.
    """
    written: list[Path] = []
    total = len(_ALL_FIXTURES)
    for index, (provider, model, modality, mode) in enumerate(_ALL_FIXTURES, 1):
        print(f"[{index}/{total}] {provider}/{model} {modality}/{mode}")
        path = await _run(provider, modality, model, mode)
        written.append(path)
    print()
    print(f"Done. {len(written)} fixtures written under {FIXTURES_DIR}.")
    print(
        "Inspect with `git diff --stat tests/fixtures/streaming/` "
        "and commit when ready."
    )
    return written


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # Default-deny: without --record, never touch a provider.
    if not args.record:
        _print_recording_disabled()
        return

    if args.all_fixtures:
        # --all is mutually exclusive with every narrowing flag.
        # Reject early so a typo does not silently ignore one of them
        # and spend money outside the apparent command intent. --mode
        # is included here because before this fix its "batch" default
        # let `--all --mode stream` slip past the mutex check (Codex
        # adversarial review medium finding).
        narrowing = {
            "--provider": args.provider,
            "--modality": args.modality,
            "--model": args.model,
            "--mode": args.mode,
        }
        offenders = [name for name, value in narrowing.items() if value]
        if offenders:
            parser.error(
                f"--all is mutually exclusive with "
                f"{', '.join(offenders)}. --all records all 6 fixtures "
                "(both batch and stream for every modality), so a "
                "narrowing flag here would be silently ignored. Pick "
                "one path: --all (no narrowing) OR --provider "
                "--modality --model --mode (single fixture)."
            )
        if not args.confirm:
            _print_all_cost_estimate()
            return
        asyncio.run(_run_all())
        return

    # --record requires the per-fixture identity flags so the cost
    # estimate and the fixture filename are unambiguous.
    if not (args.provider and args.modality and args.model):
        parser.error(
            "--provider, --modality, --model are required with --record "
            "(unless --all is used)"
        )

    # In the single-fixture path, --mode defaults to batch when omitted.
    mode = args.mode if args.mode is not None else "batch"

    # --record without --confirm: dry-run cost estimate, no API call.
    if not args.confirm:
        _print_cost_estimate(
            args.provider, args.model, args.modality, mode
        )
        return

    asyncio.run(_run(args.provider, args.modality, args.model, mode))


if __name__ == "__main__":
    main()
