"""The tests must run against the dependency tree ``uv.lock`` describes.

A CI job installed with ``uv pip install -e ".[dev,dashboard]"``, which
resolves fresh against the pyproject pins, and then ran every later step with
``uv run``, which re-syncs the project environment from ``uv.lock``. Those two
can disagree, and did: ``voice-prices>=0.3.0,<1`` resolves to 0.6.0 while the
lock pinned 0.3.0.

That is not a packaging nicety. ``voice-prices`` ships the price catalogue, so
its version decides what a model costs and whether a model is priced at all.
0.6.0 rewrote the Deepgram matchers and ``deepgram/nova-2-phonecall`` went from
unpriced to $0.00583332/minute. A test asserting it was unpriced then passed or
failed depending on which uv command had touched the environment last, which
reads exactly like flakiness and is not: each version is perfectly
deterministic on its own.

The structural fix is in ``ci.yml`` (sync from the lock, never pip-install
alongside it). This is the guard for that fix, because the structural fix is
one line someone can reasonably-looking undo.

Scoped deliberately to the catalogue dependency rather than the whole lock.
Asserting every pinned version matches would fail on any local venv that is
merely a little stale, which is noise; this one package is the one whose
version silently changes what the assertions in this suite mean.

Set ``VOICEGW_ALLOW_DEP_DRIFT=1`` to run the suite against a deliberately
different version, which is a legitimate thing to do when checking that a
change survives a dependency bump.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest
import voice_prices

_TRACKED = "voice-prices"


def _repo_root() -> Path | None:
    """Walk up for the directory holding ``uv.lock``.

    Returns None for an installed-package checkout with no lock beside it (a
    wheel install, or a source tree the lock was not shipped with), where
    there is nothing to compare against.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    return None


def _locked_version(lock: Path, name: str) -> str | None:
    data = tomllib.loads(lock.read_text())
    for package in data.get("package", []):
        if package.get("name") == name:
            version = package.get("version")
            return str(version) if version is not None else None
    return None


def test_the_installed_catalogue_matches_the_lock() -> None:
    """Fail loudly when the environment is not the one the lock describes."""
    if os.environ.get("VOICEGW_ALLOW_DEP_DRIFT"):
        pytest.skip("VOICEGW_ALLOW_DEP_DRIFT set: deliberate cross-version run")

    root = _repo_root()
    if root is None:
        pytest.skip("no uv.lock beside the package; nothing to compare against")

    locked = _locked_version(root / "uv.lock", _TRACKED)
    if locked is None:
        pytest.skip(f"{_TRACKED} is not pinned in uv.lock")

    installed = voice_prices.__version__
    assert installed == locked, (
        f"{_TRACKED} {installed} is installed but uv.lock pins {locked}. "
        f"The suite is running against a different price catalogue than the "
        f"lock describes, so any assertion about what a model costs, or about "
        f"whether it is priced at all, may pass or fail for reasons unrelated "
        f"to the code under test. Run `uv sync --extra dev --extra dashboard` "
        f"to match the lock, `uv lock --upgrade-package {_TRACKED}` to move "
        f"the lock forward on purpose, or set VOICEGW_ALLOW_DEP_DRIFT=1 if "
        f"you are deliberately testing against another version."
    )
