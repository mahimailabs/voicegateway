"""CLI gating tests for scripts/record-streaming-fixtures.py.

These tests cover the default-deny gating: --record gates real API
calls; --confirm gates past the cost-estimate dry-run. The actual
recording bodies are not exercised here; they require real API
keys and provider SDKs that live behind the recorder.

Subprocess invocation is intentional. The script is meant to be
run as a CLI; testing the full process boundary catches argparse
wiring mistakes that an in-process import would miss.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDER = REPO_ROOT / "scripts" / "record-streaming-fixtures.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RECORDER), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        cwd=REPO_ROOT,
    )


def test_recorder_script_exists_and_is_a_file() -> None:
    """Sanity: the recording script is at the documented path."""
    assert RECORDER.exists(), f"Expected recorder at {RECORDER}"
    assert RECORDER.is_file()


def test_no_record_flag_prints_disabled_banner() -> None:
    """Without --record the script must not hit any provider."""
    result = _run()
    assert result.returncode == 0, (
        f"Expected exit 0 for the disabled-banner path, got "
        f"{result.returncode}; stderr={result.stderr!r}"
    )
    assert "recording disabled, use --record" in result.stdout, (
        f"Disabled banner missing from stdout. "
        f"stdout={result.stdout!r}"
    )


def test_no_record_flag_with_full_identity_still_disabled() -> None:
    """--provider/--modality/--model alone do not bypass the --record gate."""
    result = _run(
        "--provider", "openai",
        "--modality", "llm",
        "--model", "gpt-4o-mini",
        "--mode", "batch",
    )
    assert result.returncode == 0
    assert "recording disabled" in result.stdout


def test_record_without_confirm_prints_cost_estimate() -> None:
    """--record without --confirm prints a cost estimate and exits 0."""
    result = _run(
        "--record",
        "--provider", "openai",
        "--modality", "llm",
        "--model", "gpt-4o-mini",
        "--mode", "batch",
    )
    assert result.returncode == 0, (
        f"Expected exit 0 for the dry-run path, got "
        f"{result.returncode}; stderr={result.stderr!r}"
    )
    assert "Estimated cost" in result.stdout
    assert "openai/gpt-4o-mini" in result.stdout
    assert "Pass --confirm" in result.stdout


def test_record_without_identity_flags_errors() -> None:
    """--record requires --provider, --modality, --model to be unambiguous."""
    result = _run("--record")
    assert result.returncode != 0, (
        "Expected non-zero exit when --record is passed without "
        "--provider/--modality/--model"
    )
    assert "required with --record" in result.stderr


def test_record_with_unknown_provider_errors() -> None:
    """argparse must reject providers outside the supported set."""
    result = _run(
        "--record",
        "--provider", "definitely-not-a-real-provider",
        "--modality", "llm",
        "--model", "x",
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_record_with_unknown_modality_errors() -> None:
    """argparse must reject modalities outside (stt, llm, tts)."""
    result = _run(
        "--record",
        "--provider", "openai",
        "--modality", "embedding",
        "--model", "x",
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_record_with_unknown_mode_errors() -> None:
    """argparse must reject modes outside (batch, stream)."""
    result = _run(
        "--record",
        "--provider", "openai",
        "--modality", "llm",
        "--model", "x",
        "--mode", "websocket",
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_record_dry_run_for_each_phase3_fixture() -> None:
    """Cost-estimate path covers all six Phase 3 fixture identities."""
    fixtures = [
        ("openai", "gpt-4o-mini", "llm", "batch"),
        ("openai", "gpt-4o-mini", "llm", "stream"),
        ("deepgram", "nova-3", "stt", "batch"),
        ("deepgram", "nova-3", "stt", "stream"),
        ("cartesia", "sonic-3", "tts", "batch"),
        ("cartesia", "sonic-3", "tts", "stream"),
    ]
    for provider, model, modality, mode in fixtures:
        result = _run(
            "--record",
            "--provider", provider,
            "--modality", modality,
            "--model", model,
            "--mode", mode,
        )
        assert result.returncode == 0, (
            f"{provider}/{model}/{modality}/{mode}: exit "
            f"{result.returncode}, stderr={result.stderr!r}"
        )
        assert "Estimated cost" in result.stdout, (
            f"{provider}/{model}/{modality}/{mode}: estimate missing "
            f"from stdout={result.stdout!r}"
        )
