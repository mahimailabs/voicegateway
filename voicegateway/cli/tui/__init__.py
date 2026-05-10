"""VoiceGateway terminal UI package (v0.1.1).

Stub package init so downstream code can ``from voicegateway.cli.tui
import TUIApp, run`` immediately, before Phase 2 lands the real
implementations in ``voicegateway.cli.tui.app``. The real ``TUIApp``
subclasses ``textual.app.App`` and the real ``run()`` is the
entry point the Typer ``tui`` command in ``voicegateway/cli/tui.py``
hands the resolved ``MetricsClient``.

The stubs raise ``NotImplementedError`` rather than silently no-op so
an accidental dispatch during the build phase fails loudly. Phase 2
replaces both symbols with re-exports from ``app.py``.
"""

from __future__ import annotations

from typing import Any


class TUIApp:
    """Placeholder for the real ``TUIApp``; replaced in Phase 2.

    The Phase-2 implementation will subclass ``textual.app.App``,
    take ``client: MetricsClient`` and ``is_local: bool`` in its
    constructor, mount the header / footer / four-tab
    ``ContentSwitcher``, and own the global vim ``BINDINGS``.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError(
            "TUIApp is a stub during the v0.1.1 build. Phase 2 swaps "
            "this for the textual.app.App subclass in "
            "voicegateway/cli/tui/app.py."
        )


def run(*args: Any, **kwargs: Any) -> None:
    """Placeholder for the real ``run()``; replaced in Phase 2.

    The Phase-2 implementation will instantiate ``TUIApp`` with the
    resolved ``MetricsClient`` and ``is_local`` flag, then call
    ``app.run()`` to enter the Textual event loop.
    """
    raise NotImplementedError(
        "run() is a stub during the v0.1.1 build. Phase 2 swaps "
        "this for the real entry point that wires the Typer "
        "command (`voicegw tui`) to TUIApp."
    )


__all__ = ["TUIApp", "run"]
