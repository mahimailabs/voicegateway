"""The streaming fixture loader: filename decoding and directory discovery."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from voicegateway.tests.fixtures.streaming._loader import (
    discover_fixture_paths,
    discover_fixtures,
    load_fixture,
    parse_fixture_filename,
)


def _valid_payload(modality: str = "llm", mode: str = "stream") -> dict[str, Any]:
    return {
        "metadata": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "modality": modality,
            "mode": mode,
            "recorded_at": "2026-05-04T14:32:11Z",
            "recorded_by": "tests/fixtures/streaming/record_streaming_fixtures.py",
            "voicegateway_version": "0.0.3",
        },
        "request": {"prompt": "Hi", "stream": True},
        "response_stream": [
            {"chunk_index": 0, "received_at_ms": 100, "data": {"x": 1}},
        ],
        "provider_reported_usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
        },
        "expected_cost_usd": "0.00000050",
    }


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_parse_fixture_filename() -> None:
    decoded = parse_fixture_filename("openai_gpt-4o-mini_llm_stream_2026-05-04.json")
    assert decoded.provider == "openai"
    assert decoded.model_slug == "gpt-4o-mini"
    assert decoded.modality == "llm"
    assert decoded.mode == "stream"
    assert decoded.recorded_date == date(2026, 5, 4)
    assert decoded.stem == "openai_gpt-4o-mini_llm_stream_2026-05-04"


def test_parse_fixture_filename_handles_underscored_model_slug() -> None:
    """Model slugs flatten ``/`` and ``:`` to ``_`` so they may contain underscores."""
    decoded = parse_fixture_filename("ollama_qwen2.5_3b_llm_batch_2026-05-04.json")
    assert decoded.provider == "ollama"
    assert decoded.model_slug == "qwen2.5_3b"
    assert decoded.modality == "llm"


@pytest.mark.parametrize(
    ("name", "match"),
    [
        ("openai_gpt-4o-mini_llm_stream_2026-05-04.txt", r"end in \.json"),
        ("openai_gpt-4o-mini_llm.json", "5"),
        ("openai_gpt_embedding_stream_2026-05-04.json", "modality"),
        ("openai_gpt-4_llm_websocket_2026-05-04.json", "mode"),
        ("openai_gpt-4_llm_stream_2026-13-99.json", "YYYY-MM-DD"),
    ],
)
def test_parse_fixture_filename_rejects(name: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_fixture_filename(name)


def test_load_fixture_parses_valid_json(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "openai_gpt-4o-mini_llm_stream_2026-05-04.json", _valid_payload()
    )
    fixture = load_fixture(p)
    assert fixture.metadata.provider == "openai"
    assert fixture.expected_cost_usd == Decimal("0.00000050")


def test_discover_skips_unrelated_files_and_orders_by_name(tmp_path: Path) -> None:
    """Sorted by filename so pytest parametrize ids are stable across runs."""
    _write(tmp_path / "unrelated.json", _valid_payload())
    names = [
        f"{p}_gpt-4o-mini_llm_batch_2026-05-04.json" for p in ("gamma", "alpha", "beta")
    ]
    for name in names:
        payload = _valid_payload(mode="batch")
        payload["metadata"]["recorded_by"] = f"tagged-{name}"
        _write(tmp_path / name, payload)

    paths = discover_fixture_paths(tmp_path)
    assert [p.name for p in paths] == sorted(names)
    fixtures = discover_fixtures(tmp_path)
    assert [f.metadata.recorded_by for f in fixtures] == [
        f"tagged-{n}" for n in sorted(names)
    ]


def test_discover_fixtures_propagates_schema_failure(tmp_path: Path) -> None:
    """A conforming filename with malformed contents fails loudly, not silently."""
    payload = _valid_payload()
    payload["expected_cost_usd"] = "-1"
    _write(tmp_path / "openai_gpt-4o-mini_llm_batch_2026-05-04.json", payload)
    with pytest.raises(ValidationError):
        discover_fixtures(tmp_path)
