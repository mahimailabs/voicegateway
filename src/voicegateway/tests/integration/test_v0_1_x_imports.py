"""Contract test: every supported public import path resolves post-refactor."""

from __future__ import annotations

# ---------- voicegateway.server (T06: __init__ is the shim) ----------


def test_voicegateway_server_build_app_resolves() -> None:
    """``from voicegateway.server import build_app`` survives the"""
    from voicegateway.server import build_app

    assert callable(build_app)


def test_voicegateway_server_all_lists_build_app() -> None:
    """The new ``server/__init__.py`` declares ``__all__`` with the"""
    import voicegateway.server as server_pkg

    assert hasattr(server_pkg, "__all__")
    assert "build_app" in server_pkg.__all__


# ---------- voicegateway.server.main (combined + API consolidated in v0.6.0) ----------


def test_voicegateway_server_main_resolves() -> None:
    """``build_app`` and ``main`` are importable from ``server.main``."""
    from voicegateway.server.main import build_app, main

    assert callable(build_app)
    assert callable(main)


# ---------- voicegateway.services.reconciliation_service (T08: __init__ is the shim) ----------


def test_voicegateway_reconcile_module_namespace_resolves() -> None:
    """``from voicegateway.services import reconciliation_service as reconcile`` resolves, and the"""
    from voicegateway.services import reconciliation_service as reconcile

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
    """The new ``reconcile/__init__.py`` declares ``__all__`` with"""
    import voicegateway.services.reconciliation_service as rec_pkg

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
    """``from voicegateway import inference`` (the drop-in"""
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
    """``from voicegateway import __version__`` works (hatch-vcs"""
    from voicegateway import __version__

    assert isinstance(__version__, str)
    assert __version__  # non-empty


def test_voicegateway_cli_app_resolves() -> None:
    """``from voicegateway.cli import app`` works -- this is the"""
    from voicegateway.cli import app

    assert app is not None
