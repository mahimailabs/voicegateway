"""Helpers for ``voicegateway.cli.init_cli``."""

from __future__ import annotations

from importlib import resources


def _read_example_config() -> str:
    """Return the full annotated example config shipped with the wheel."""
    return (
        resources.files("voicegateway.data")
        .joinpath("voicegw.example.yaml")
        .read_text(encoding="utf-8")
    )


def _read_minimal_config() -> str:
    """Return the short, beginner-friendly starter config shipped with the wheel."""
    return (
        resources.files("voicegateway.data")
        .joinpath("voicegw.minimal.yaml")
        .read_text(encoding="utf-8")
    )
