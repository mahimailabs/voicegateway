"""Smoke tests for :class:`BaseCli`."""

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


@pytest.mark.parametrize(
    "method,marker",
    [
        ("error", "boom"),
        ("success", "ok"),
        ("warn", "watch out"),
        ("info", "fyi"),
        ("dim", "quiet note"),
    ],
)
def test_styled_output_methods_print(method: str, marker: str) -> None:
    cli, _, buf = _make_cli()
    getattr(cli, method)(marker)
    assert marker in buf.getvalue()


def test_fail_raises_typer_exit_with_code() -> None:
    cli, _, buf = _make_cli()
    with pytest.raises(typer.Exit) as excinfo:
        cli.fail("nope", code=7)
    assert excinfo.value.exit_code == 7
    assert "nope" in buf.getvalue()


def test_fail_defaults_to_exit_code_one() -> None:
    cli, _, _ = _make_cli()
    with pytest.raises(typer.Exit) as excinfo:
        cli.fail("default")
    assert excinfo.value.exit_code == 1


def test_require_storage_returns_storage_when_present() -> None:
    cli, _, _ = _make_cli()
    sentinel = object()
    gw = SimpleNamespace(storage=sentinel)
    assert cli.require_storage(gw) is sentinel


def test_require_storage_fails_when_storage_is_none() -> None:
    cli, _, buf = _make_cli()
    gw = SimpleNamespace(storage=None)
    with pytest.raises(typer.Exit) as excinfo:
        cli.require_storage(gw)
    assert excinfo.value.exit_code == 1
    assert "Storage backend not configured" in buf.getvalue()


def test_async_run_executes_coroutine() -> None:
    async def _value() -> int:
        return 42

    cli, _, _ = _make_cli()
    assert cli.async_run(_value()) == 42


def test_default_console_falls_back_to_shared_singleton() -> None:
    from voicegateway.cli._app import console as shared

    cli = BaseCli()
    assert cli.console is shared


def test_require_gateway_propagates_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Gateway built by ``require_gateway`` should be returned unchanged."""
    sentinel = object()

    class _FakeGateway:
        def __init__(self, config_path: str | None = None) -> None:
            assert config_path == "/tmp/voicegw.yaml"

        def __new__(cls, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            return sentinel

    monkeypatch.setattr("voicegateway.core.gateway.Gateway", _FakeGateway)
    cli, _, _ = _make_cli()
    assert cli.require_gateway("/tmp/voicegw.yaml") is sentinel


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
