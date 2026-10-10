"""The runnable examples import clean.

``examples/livekit_attach_guard.py`` and ``examples/pipecat_attach_guard.py``
must import without error (their native provider imports are deferred into
helper functions, so importing the module never requires the provider wheels or
API keys). This guards the docs' copy-paste path against a stale example.

attach() + guard() composition is covered in test_guard.py
(no double count) and test_guard_pipecat.py (fallback stamp), and a live
Pipecat pipeline in test_pipecat_e2e.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

# examples/ sits at the repo root: .../voicegateway/examples/*.py. This file is
# at src/voicegateway/tests/inference/, so walk up to the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_EXAMPLES = _REPO_ROOT / "examples"
_LIVEKIT_EXAMPLE = _EXAMPLES / "livekit_attach_guard.py"
_PIPECAT_EXAMPLE = _EXAMPLES / "pipecat_attach_guard.py"


# --- examples import ---------------------------------------------------------


def _import_example(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # ImportError here fails the test
    return module


def test_livekit_example_imports() -> None:
    """The LiveKit example imports (deferred plugin imports keep it clean)."""
    module = _import_example(_LIVEKIT_EXAMPLE)
    # The public entry points exist and are callable.
    assert callable(module.build_session)
    assert callable(module.entrypoint)


def test_pipecat_example_imports() -> None:
    """The Pipecat example imports (deferred service imports keep it clean)."""
    module = _import_example(_PIPECAT_EXAMPLE)
    assert callable(module.build_task)
    assert callable(module.run)
