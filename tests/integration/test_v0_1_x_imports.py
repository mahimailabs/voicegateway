"""Contract test: every v0.1.x public import path resolves post-refactor.

The v0.1.2 project-polish refactor (REQ-VG-POLISH-004) split three
top-level modules into subpackages:

- ``voicegateway/server.py`` -> ``voicegateway/server/{__init__,main}.py``
- ``voicegateway/combined_server.py`` -> ``voicegateway/server/combined.py``
  with a re-export shim at the original path.
- ``voicegateway/reconcile.py`` -> ``voicegateway/reconcile/{__init__,core}.py``

REQ-VG-POLISH-004 AC-2 mandates that every v0.1.x import path that
worked before v0.1.2 keeps working. This file is the canary for that
contract: if any of these imports breaks, downstream users running
v0.1.x code against v0.1.2 will hit ImportError. Failing here on PR/CI
catches that before release.

Scope: the imports v0.1.x callers actually use. Internal-only imports
under leading-underscore modules are NOT in scope (those are part of
the private surface and may change without a deprecation cycle).
"""

from __future__ import annotations

import importlib.util

# ---------- voicegateway.server (T06: __init__ is the shim) ----------


def test_voicegateway_server_build_app_resolves() -> None:
    """``from voicegateway.server import build_app`` survives the
    v0.1.2 split. The 8 v0.1.x call sites
    (``voicegateway.cli.serve``, the 5 server tests,
    ``voicegateway.combined_server``, and the shim self-reference)
    all rely on this path."""
    from voicegateway.server import build_app

    assert callable(build_app)


def test_voicegateway_server_all_lists_build_app() -> None:
    """The new ``server/__init__.py`` declares ``__all__`` with the
    single public symbol."""
    import voicegateway.server as server_pkg

    assert hasattr(server_pkg, "__all__")
    assert "build_app" in server_pkg.__all__


# ---------- voicegateway.combined_server (T07: shim at old path) ----------


def test_voicegateway_combined_server_build_combined_app_resolves() -> None:
    """The combined_server shim re-exports build_combined_app for the
    one v0.1.x test that imports it
    (``tests/server/test_combined_server.py``)."""
    from voicegateway.combined_server import build_combined_app

    assert callable(build_combined_app)


def test_voicegateway_combined_server_main_resolves() -> None:
    """The shim also exports ``main()`` so the entry path
    ``python -m voicegateway.combined_server`` keeps working."""
    from voicegateway.combined_server import main

    assert callable(main)


def test_voicegateway_combined_server_module_is_runnable() -> None:
    """Verify the shim path is a real module spec, not just a name."""
    spec = importlib.util.find_spec("voicegateway.combined_server")

    assert spec is not None, "voicegateway.combined_server must be importable"
    assert spec.origin is not None, "shim must have a concrete .py file"
    assert spec.origin.endswith("combined_server.py"), spec.origin


def test_voicegateway_combined_server_canonical_path_also_resolves() -> None:
    """The new canonical path also resolves; the shim is a parallel
    name, not a replacement."""
    from voicegateway.server.combined import build_combined_app, main

    assert callable(build_combined_app)
    assert callable(main)


# ---------- voicegateway.reconcile (T08: __init__ is the shim) ----------


def test_voicegateway_reconcile_module_namespace_resolves() -> None:
    """``from voicegateway import reconcile`` resolves, and the
    attribute-access surface covers every name v0.1.x callers use
    (``voicegateway/cli/reconcile.py`` and the integration tests
    both reach into the namespace)."""
    from voicegateway import reconcile

    # Functions
    assert callable(reconcile.parse_provider_file)
    assert callable(reconcile.aggregate_vg_records)
    assert callable(reconcile.reconcile)
    assert callable(reconcile.format_text)
    assert callable(reconcile.format_csv)
    assert callable(reconcile.format_json)
    # Constants
    assert reconcile.SUPPORTED_PROVIDERS  # non-empty tuple
    assert isinstance(reconcile.DEFAULT_DIFF_THRESHOLD_PCT, float)
    # Class
    assert reconcile.ReconcileLine is not None


def test_voicegateway_reconcile_all_lists_full_surface() -> None:
    """The new ``reconcile/__init__.py`` declares ``__all__`` with
    every public symbol."""
    import voicegateway.reconcile as rec_pkg

    expected = {
        "DEFAULT_DIFF_THRESHOLD_PCT",
        "ReconcileLine",
        "SUPPORTED_PROVIDERS",
        "aggregate_vg_records",
        "format_csv",
        "format_json",
        "format_text",
        "parse_provider_file",
        "reconcile",
    }
    assert hasattr(rec_pkg, "__all__")
    assert set(rec_pkg.__all__) == expected


# ---------- v0.0.5+ public surface (unchanged by T06-T08) ----------


def test_voicegateway_top_level_inference_resolves() -> None:
    """``from voicegateway import inference`` (the drop-in
    LiveKit-parity surface from v0.0.5) still works."""
    from voicegateway import inference

    assert hasattr(inference, "LLM")
    assert hasattr(inference, "STT")
    assert hasattr(inference, "TTS")


def test_voicegateway_inference_factories_resolve() -> None:
    """``from voicegateway.inference import LLM, STT, TTS`` works."""
    from voicegateway.inference import LLM, STT, TTS

    assert callable(LLM)
    assert callable(STT)
    assert callable(TTS)


def test_voicegateway_version_resolves() -> None:
    """``from voicegateway import __version__`` works (hatch-vcs
    generated, exported via ``voicegateway/__init__.py``)."""
    from voicegateway import __version__

    assert isinstance(__version__, str)
    assert __version__  # non-empty


def test_voicegateway_cli_app_resolves() -> None:
    """``from voicegateway.cli import app`` works -- this is the
    console-script entry point declared in pyproject.toml as
    ``voicegw = "voicegateway.cli:app"``."""
    from voicegateway.cli import app

    assert app is not None
