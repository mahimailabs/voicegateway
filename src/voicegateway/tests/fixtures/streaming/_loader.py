"""Loader for Phase 3 streaming fixtures.

Reads fixture JSON files from this directory, validates them against
the schema in `_schema.py`, and exposes them to the replay tests
in `tests/test_streaming_cost_accounting.py`.

Public API:

- ``load_fixture(path)`` returns a validated ``StreamingFixture``.
- ``discover_fixtures(directory=...)`` returns every fixture file in
  the directory, parsed and validated.
- ``parse_fixture_filename(path)`` extracts the (provider, model
  slug, modality, mode, recorded date) tuple encoded in the
  filename. The staleness check uses this; pytest parametrize ids
  use it for human-readable test names.

The loader has no dependency on `respx` or any provider SDK; it is
safe to import from any test or script.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from voicegateway.tests.fixtures.streaming._schema import StreamingFixture

FIXTURES_DIR: Path = Path(__file__).parent


@dataclass(frozen=True)
class FixtureFilename:
    """Decoded components of a fixture filename.

    Filenames follow ``<provider>_<model_slug>_<modality>_<mode>_<YYYY-MM-DD>.json``,
    per design §3.2. Model slugs may contain underscores (e.g.
    ``ollama_qwen2.5_3b``), so parsing happens from the right.
    """

    provider: str
    model_slug: str
    modality: str
    mode: str
    recorded_date: date

    @property
    def stem(self) -> str:
        return (
            f"{self.provider}_{self.model_slug}_{self.modality}_"
            f"{self.mode}_{self.recorded_date.isoformat()}"
        )


def parse_fixture_filename(path: Path | str) -> FixtureFilename:
    """Decode a fixture filename into its five components.

    Raises ``ValueError`` if the filename does not match the locked
    convention. The error message names which field failed so a
    misnamed fixture surfaces immediately.
    """
    p = Path(path)
    if p.suffix != ".json":
        raise ValueError(f"Fixture filename {p.name!r} must end in .json")
    parts = p.stem.split("_")
    if len(parts) < 5:
        raise ValueError(
            f"Fixture filename {p.name!r} does not have at least 5 "
            "underscore-separated parts (provider, model slug, "
            "modality, mode, YYYY-MM-DD)."
        )

    date_str = parts[-1]
    mode = parts[-2]
    modality = parts[-3]
    provider = parts[0]
    model_slug = "_".join(parts[1:-3])

    if modality not in {"stt", "llm", "tts"}:
        raise ValueError(
            f"Fixture filename {p.name!r}: modality {modality!r} "
            "must be one of stt, llm, tts."
        )
    if mode not in {"batch", "stream"}:
        raise ValueError(
            f"Fixture filename {p.name!r}: mode {mode!r} must be one of batch, stream."
        )
    try:
        recorded_date = date.fromisoformat(date_str)
    except ValueError as err:
        raise ValueError(
            f"Fixture filename {p.name!r}: date {date_str!r} is not a valid YYYY-MM-DD."
        ) from err
    if not provider:
        raise ValueError(f"Fixture filename {p.name!r}: provider segment empty.")
    if not model_slug:
        raise ValueError(f"Fixture filename {p.name!r}: model slug segment empty.")

    return FixtureFilename(
        provider=provider,
        model_slug=model_slug,
        modality=modality,
        mode=mode,
        recorded_date=recorded_date,
    )


def load_fixture(path: Path | str) -> StreamingFixture:
    """Load and validate a single fixture JSON file.

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        json.JSONDecodeError: if the file is not valid JSON.
        pydantic.ValidationError: if the JSON shape does not match
            ``StreamingFixture``.
    """
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    payload = json.loads(raw)
    return StreamingFixture.model_validate(payload)


def _iter_fixture_files(directory: Path) -> Iterator[Path]:
    """Yield every ``*.json`` file in ``directory`` in deterministic order.

    Sorted by name so pytest's parametrize ids are stable across
    runs.
    """
    yield from sorted(directory.glob("*.json"))


def discover_fixtures(directory: Path | str | None = None) -> list[StreamingFixture]:
    """Return every fixture in ``directory`` (default: this dir), validated.

    A fixture file is any ``*.json`` whose name decodes via
    ``parse_fixture_filename``. Files that fail filename parsing are
    skipped silently (so unrelated JSON in the directory cannot
    derail discovery), but files that pass filename parsing and
    fail schema validation raise loudly.
    """
    base = Path(directory) if directory is not None else FIXTURES_DIR
    fixtures: list[StreamingFixture] = []
    for fixture_file in _iter_fixture_files(base):
        try:
            parse_fixture_filename(fixture_file)
        except ValueError:
            continue
        fixtures.append(load_fixture(fixture_file))
    return fixtures


def discover_fixture_paths(
    directory: Path | str | None = None,
) -> list[Path]:
    """Return every conforming fixture path in ``directory``.

    Useful for pytest parametrize when the test wants the filename
    string in addition to the parsed model.
    """
    base = Path(directory) if directory is not None else FIXTURES_DIR
    out: list[Path] = []
    for fixture_file in _iter_fixture_files(base):
        try:
            parse_fixture_filename(fixture_file)
        except ValueError:
            continue
        out.append(fixture_file)
    return out
