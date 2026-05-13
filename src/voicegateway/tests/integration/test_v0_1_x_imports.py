"""Contract test: every supported public import path resolves post-refactor.

The v0.1.2 project-polish refactor (REQ-VG-POLISH-004) split two
top-level modules into subpackages, plus a now-retired third:

- ``voicegateway/server.py`` -> ``voicegateway/server/{__init__,main}.py``
- ``voicegateway/reconcile.py`` -> ``voicegateway/reconcile/{__init__,core}.py``
- ``voicegateway/combined_server.py`` was a re-export shim through v0.5.0;
  v0.6.0 retires the shim. Callers must use
  ``from voicegateway.server.combined import build_combined_app, main``.

REQ-VG-POLISH-004 AC-2 mandates that every supported import path keeps
working. This file is the canary for that contract: if any of these
imports breaks, downstream code will hit ImportError. Failing here on
PR/CI catches that before release.

Scope: the imports callers actually use. Internal-only imports under
leading-underscore modules are NOT in scope (those are part of the
private surface and may change without a deprecation cycle).
"""

from __future__ import annotations

# ---------- voicegateway.server (T06: __init__ is the shim) ----------


def test_voicegateway_server_build_app_resolves() -> None:
    """``from voicegateway.server import build_app`` survives the
    v0.1.2 split. ``voicegateway.cli.serve``, the server tests, and
    ``voicegateway.server.combined`` all rely on this path."""
    from voicegateway.server import build_app

    assert callable(build_app)


def test_voicegateway_server_all_lists_build_app() -> None:
    """The new ``server/__init__.py`` declares ``__all__`` with the
    single public symbol."""
    import voicegateway.server as server_pkg

    assert hasattr(server_pkg, "__all__")
    assert "build_app" in server_pkg.__all__


# ---------- voicegateway.server.combined (canonical; shim retired in v0.6.0) ----------


def test_voicegateway_server_combined_resolves() -> None:
    """The canonical combined-server path resolves. The
    ``voicegateway.combined_server`` re-export shim was retired in
    v0.6.0; callers must import from ``voicegateway.server.combined``."""
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
