"""Helpers for ``voicegateway.cli.init``."""

from __future__ import annotations

from importlib import resources


def _read_example_config() -> str:
    """Return the canonical example config shipped with the wheel."""
    return (
        resources.files("voicegateway.data")
        .joinpath("voicegw.example.yaml")
        .read_text(encoding="utf-8")
    )
