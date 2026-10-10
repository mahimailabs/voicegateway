"""Spend gates on tests/fixtures/streaming/record_streaming_fixtures.py.

The recorder calls paid provider APIs. What matters is that it never does so
unless explicitly told to twice (--record and --confirm).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RECORDER = (
    REPO_ROOT / "tests" / "fixtures" / "streaming" / "record_streaming_fixtures.py"
)
_IDENTITY = ("--provider", "openai", "--modality", "llm", "--model", "gpt-4o-mini")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    # No provider keys: a dry run must succeed without them, i.e. make no call.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"OPENAI_API_KEY", "DEEPGRAM_API_KEY", "CARTESIA_API_KEY"}
    }
    return subprocess.run(
        [sys.executable, str(RECORDER), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        cwd=REPO_ROOT,
        env=env,
    )


def test_identity_flags_without_record_stay_disabled() -> None:
    result = _run(*_IDENTITY, "--mode", "batch")
    assert result.returncode == 0, result.stderr
    assert "recording disabled, use --record" in result.stdout


def test_record_without_confirm_only_estimates() -> None:
    """--mode is optional and defaults to batch."""
    result = _run("--record", *_IDENTITY)
    assert result.returncode == 0, result.stderr
    assert "Estimated cost" in result.stdout
    assert "openai/gpt-4o-mini" in result.stdout
    assert "llm/batch" in result.stdout
    assert "Pass --confirm" in result.stdout


def test_all_without_confirm_only_estimates() -> None:
    result = _run("--record", "--all")
    assert result.returncode == 0, result.stderr
    assert "About to record all 6 Phase 3 fixtures" in result.stdout
    assert "Estimated total: ~$" in result.stdout
    assert "expected_cost_usd" not in result.stdout


@pytest.mark.parametrize(
    ("args", "stderr_marker"),
    [
        (("--record",), "required with --record"),
        (("--record", *_IDENTITY, "--mode", "websocket"), "invalid choice"),
        (("--record", "--all", *_IDENTITY), "mutually exclusive"),
        (("--record", "--all", "--mode", "batch"), "mutually exclusive"),
    ],
    ids=["no-identity", "bad-mode", "all-plus-identity", "all-plus-mode"],
)
def test_ambiguous_invocations_are_rejected(args, stderr_marker) -> None:
    result = _run(*args)
    assert result.returncode != 0
    assert stderr_marker in result.stderr
