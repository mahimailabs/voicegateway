"""Unit tests for the streaming fixture loader.

These tests work against synthetic fixture files written into
tmp_path. They do not depend on real Phase 3 fixtures landing.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.fixtures.streaming._loader import (
    FixtureFilename,
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
            "recorded_by": "scripts/record_streaming_fixtures.py",
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


# ---------- parse_fixture_filename --------------------------------------


def test_parse_fixture_filename_basic() -> None:
    decoded = parse_fixture_filename(
        "openai_gpt-4o-mini_llm_stream_2026-05-04.json"
    )
    assert decoded.provider == "openai"
    assert decoded.model_slug == "gpt-4o-mini"
    assert decoded.modality == "llm"
    assert decoded.mode == "stream"
    assert decoded.recorded_date == date(2026, 5, 4)


def test_parse_fixture_filename_handles_underscored_model_slug() -> None:
    """Model slugs flatten ``/`` and ``:`` to ``_`` so they may contain underscores."""
    decoded = parse_fixture_filename(
        "ollama_qwen2.5_3b_llm_batch_2026-05-04.json"
    )
    assert decoded.provider == "ollama"
    assert decoded.model_slug == "qwen2.5_3b"
    assert decoded.modality == "llm"


def test_parse_fixture_filename_accepts_path_object() -> None:
    decoded = parse_fixture_filename(
        Path("/tmp/whatever/deepgram_nova-3_stt_batch_2026-05-04.json")
    )
    assert decoded.provider == "deepgram"
    assert decoded.modality == "stt"
    assert decoded.recorded_date == date(2026, 5, 4)


def test_parse_fixture_filename_rejects_non_json_suffix() -> None:
    with pytest.raises(ValueError, match=r"end in \.json"):
        parse_fixture_filename("openai_gpt-4o-mini_llm_stream_2026-05-04.txt")


def test_parse_fixture_filename_rejects_too_few_parts() -> None:
    with pytest.raises(ValueError, match="5"):
        parse_fixture_filename("openai_gpt-4o-mini_llm.json")


def test_parse_fixture_filename_rejects_unknown_modality() -> None:
    with pytest.raises(ValueError, match="modality"):
        parse_fixture_filename("openai_gpt_embedding_stream_2026-05-04.json")


def test_parse_fixture_filename_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        parse_fixture_filename("openai_gpt-4_llm_websocket_2026-05-04.json")


def test_parse_fixture_filename_rejects_invalid_date() -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_fixture_filename("openai_gpt-4_llm_stream_2026-13-99.json")


def test_fixture_filename_stem_round_trips() -> None:
    name = "openai_gpt-4o-mini_llm_stream_2026-05-04"
    decoded = parse_fixture_filename(name + ".json")
    assert decoded.stem == name


# ---------- load_fixture -----------------------------------------------


def test_load_fixture_parses_valid_json(tmp_path: Path) -> None:
    from decimal import Decimal

    p = _write(
        tmp_path / "openai_gpt-4o-mini_llm_stream_2026-05-04.json",
        _valid_payload(),
    )
    fixture = load_fixture(p)
    assert fixture.metadata.provider == "openai"
    assert fixture.metadata.modality == "llm"
    assert fixture.expected_cost_usd == Decimal("0.00000050")


def test_load_fixture_rejects_malformed_json(tmp_path: Path) -> None:
    p = tmp_path / "openai_gpt-4o-mini_llm_stream_2026-05-04.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_fixture(p)


def test_load_fixture_rejects_schema_violation(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["metadata"]["modality"] = "embedding"  # not a valid literal
    p = _write(
        tmp_path / "openai_gpt-4o-mini_llm_stream_2026-05-04.json",
        payload,
    )
    with pytest.raises(ValidationError):
        load_fixture(p)


def test_load_fixture_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_fixture(tmp_path / "missing.json")


def test_load_fixture_accepts_string_path(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "openai_gpt-4o-mini_llm_stream_2026-05-04.json",
        _valid_payload(),
    )
    fixture = load_fixture(str(p))
    assert fixture.metadata.provider == "openai"


# ---------- discover_fixtures ------------------------------------------


def test_discover_fixtures_returns_empty_for_empty_dir(tmp_path: Path) -> None:
    assert discover_fixtures(tmp_path) == []


def test_discover_fixtures_returns_validated_models(tmp_path: Path) -> None:
    _write(
        tmp_path / "openai_gpt-4o-mini_llm_batch_2026-05-04.json",
        _valid_payload(modality="llm", mode="batch"),
    )
    _write(
        tmp_path / "openai_gpt-4o-mini_llm_stream_2026-05-04.json",
        _valid_payload(modality="llm", mode="stream"),
    )
    fixtures = discover_fixtures(tmp_path)
    assert len(fixtures) == 2
    modes = {f.metadata.mode for f in fixtures}
    assert modes == {"batch", "stream"}


def test_discover_fixtures_skips_non_conforming_filenames(tmp_path: Path) -> None:
    """An unrelated JSON file in the dir does not derail discovery."""
    _write(tmp_path / "unrelated.json", _valid_payload())
    _write(
        tmp_path / "openai_gpt-4o-mini_llm_batch_2026-05-04.json",
        _valid_payload(modality="llm", mode="batch"),
    )
    fixtures = discover_fixtures(tmp_path)
    assert len(fixtures) == 1
    assert fixtures[0].metadata.mode == "batch"


def test_discover_fixtures_orders_results_deterministically(tmp_path: Path) -> None:
    """Sort by filename so pytest parametrize ids are stable across runs."""
    a = "alpha_gpt-4o-mini_llm_batch_2026-05-04.json"
    b = "beta_gpt-4o-mini_llm_batch_2026-05-04.json"
    c = "gamma_gpt-4o-mini_llm_batch_2026-05-04.json"
    # Vary recorded_by per fixture so the loaded models are
    # distinguishable; otherwise _valid_payload yields three
    # identical StreamingFixture instances and ordering can't be
    # observed from the returned list.
    for name in (c, a, b):
        payload = _valid_payload(modality="llm", mode="batch")
        payload["metadata"]["recorded_by"] = f"tagged-{name}"
        _write(tmp_path / name, payload)

    fixture_paths = discover_fixture_paths(tmp_path)
    names = [p.name for p in fixture_paths]
    assert names == sorted(names), (
        f"discover_fixture_paths must return paths sorted by name; "
        f"got {names!r}"
    )

    fixtures = discover_fixtures(tmp_path)
    assert len(fixtures) == len(fixture_paths) == 3

    # discover_fixtures must preserve the same order as
    # discover_fixture_paths so pytest parametrize ids stay stable
    # across runs. Cross-check via the recorded_by tags.
    expected_tags = [f"tagged-{n}" for n in sorted(names)]
    actual_tags = [f.metadata.recorded_by for f in fixtures]
    assert actual_tags == expected_tags, (
        f"discover_fixtures order differs from discover_fixture_paths; "
        f"expected {expected_tags!r}, got {actual_tags!r}"
    )


def test_discover_fixtures_propagates_schema_failure(tmp_path: Path) -> None:
    """A conforming filename with malformed contents fails loudly, not silently."""
    payload = _valid_payload()
    payload["expected_cost_usd"] = "-1"  # validator rejects negative
    _write(
        tmp_path / "openai_gpt-4o-mini_llm_batch_2026-05-04.json",
        payload,
    )
    with pytest.raises(ValidationError):
        discover_fixtures(tmp_path)


def test_discover_fixture_paths_skips_non_conforming(tmp_path: Path) -> None:
    _write(tmp_path / "random.json", _valid_payload())
    _write(
        tmp_path / "openai_gpt-4o-mini_llm_batch_2026-05-04.json",
        _valid_payload(),
    )
    paths = discover_fixture_paths(tmp_path)
    assert len(paths) == 1
    assert paths[0].name == "openai_gpt-4o-mini_llm_batch_2026-05-04.json"


def test_default_directory_is_this_package() -> None:
    """When called with no argument, discover_fixtures scans this directory."""
    from tests.fixtures.streaming import _loader

    assert _loader.FIXTURES_DIR.name == "streaming"
    assert (_loader.FIXTURES_DIR / "_loader.py").exists()


def test_filename_dataclass_is_frozen() -> None:
    """Mutation must raise FrozenInstanceError, not pass silently."""
    import dataclasses

    decoded = FixtureFilename(
        provider="x",
        model_slug="y",
        modality="llm",
        mode="batch",
        recorded_date=date(2026, 1, 1),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        decoded.provider = "mutated"  # type: ignore[misc]
    # Field unchanged after the rejected assignment.
    assert decoded.provider == "x"
