"""Load the Wave 0 security fixtures off disk.

Mirrors ``fixtures/streaming/_loader.py``: the JSON files are the source of
truth, the loader validates them into typed objects, and every consumer goes
through here so a malformed fixture fails in one place.
"""

from __future__ import annotations

import json
from pathlib import Path

from voicegateway.tests.fixtures.security._schema import SecurityFixture

FIXTURE_DIR = Path(__file__).resolve().parent


def fixture_paths() -> list[Path]:
    """Return every case file, sorted, ignoring the private modules."""
    return sorted(FIXTURE_DIR.glob("*.json"))


def load_fixture(path: Path) -> SecurityFixture:
    """Validate one case file."""
    return SecurityFixture.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_all() -> list[SecurityFixture]:
    """Validate every case file."""
    return [load_fixture(path) for path in fixture_paths()]


def by_kind(kind: str) -> list[SecurityFixture]:
    """Return every case of one kind."""
    return [f for f in load_all() if f.kind == kind]


__all__ = ["FIXTURE_DIR", "by_kind", "fixture_paths", "load_all", "load_fixture"]
