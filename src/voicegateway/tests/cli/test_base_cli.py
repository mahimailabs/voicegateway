"""BaseCli failure paths exit non-zero with a readable message."""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pytest
import typer
from rich.console import Console

from voicegateway.cli.base_cli import BaseCli


def _make_cli() -> tuple[BaseCli, Console, io.StringIO]:
    """Return a BaseCli that prints into a StringIO-backed Console."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    cli = BaseCli(console=console)
    return cli, console, buf


def test_fail_raises_typer_exit_with_code() -> None:
    cli, _, buf = _make_cli()
    with pytest.raises(typer.Exit) as excinfo:
        cli.fail("nope", code=7)
    assert excinfo.value.exit_code == 7
    assert "nope" in buf.getvalue()


def test_require_storage_fails_when_storage_is_none() -> None:
    cli, _, buf = _make_cli()
    gw = SimpleNamespace(storage=None)
    with pytest.raises(typer.Exit) as excinfo:
        cli.require_storage(gw)
    assert excinfo.value.exit_code == 1
    assert "Storage backend not configured" in buf.getvalue()


def test_require_gateway_fails_on_constructor_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenGateway:
        def __init__(self, **_kwargs: Any) -> None:
            raise RuntimeError("config bad")

    monkeypatch.setattr("voicegateway.core.gateway.Gateway", _BrokenGateway)
    cli, _, buf = _make_cli()
    with pytest.raises(typer.Exit) as excinfo:
        cli.require_gateway(None)
    assert excinfo.value.exit_code == 1
    assert "Error loading config" in buf.getvalue()
    assert "config bad" in buf.getvalue()
