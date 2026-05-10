"""VoiceGateway terminal UI package (v0.1.1).

Re-exports the Phase-2 :class:`TUIApp` from
``voicegateway.cli.tui.app`` so downstream code can
``from voicegateway.cli.tui import TUIApp`` without reaching into
the implementation module.

``run()`` stays a stub until the Phase-2 Typer-command bullet
(``voicegateway/cli/tui.py``) lands; the real entry point
instantiates ``TUIApp`` with the resolved
:class:`MetricsClient` returned by
``voicegateway.cli.tui.data.factory.make_client`` and calls
``app.run()`` to enter the Textual event loop.
"""

from __future__ import annotations

from typing import Any

from voicegateway.cli.tui.app import TUIApp


def run(*args: Any, **kwargs: Any) -> None:
    """Placeholder for the real ``run()``; replaced when the Typer
    command lands.

    The real entry point is a thin shim that resolves the launch
    flags via ``factory.make_client`` and hands the result to
    :class:`TUIApp`. Until that bullet lands, calling this stub
    raises so a partial-build dispatch fails loudly instead of
    silently no-op-ing.
    """
    raise NotImplementedError(
        "run() is a stub until the Phase-2 Typer command (`voicegw "
        "tui`) lands. Instantiate TUIApp directly for now."
    )


__all__ = ["TUIApp", "run"]
