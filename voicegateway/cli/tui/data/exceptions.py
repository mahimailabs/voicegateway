"""TUI data-layer exceptions.

Single class on purpose. The Foundry blueprint's open question 4 was
resolved as: one ``LocalModeUnsupportedError`` carrying a ``feature``
attribute, not a class hierarchy per unsupported reason. That keeps
the surface flat for the Providers screen's ``t`` shortcut (which is
the only call site today) and lets the message render the feature
verbatim without a switch over subclass types.

If v0.2.x grows multiple structurally-different unsupported reasons
(auth-required, license-gated, modality-gated, etc.), promote the
``feature`` field to a ``StrEnum`` rather than splitting into
subclasses; the error class itself stays one.
"""

from __future__ import annotations


class LocalModeUnsupportedError(Exception):
    """Raised by ``LocalClient`` write methods.

    The TUI's Providers screen catches this on the ``t`` shortcut to
    render a one-line ``requires daemon`` hint at the foot of the
    panel; no silent failure, no stack trace.

    Attributes:
        feature: machine-readable feature id (e.g. ``"test_provider"``)
            so callers can branch on identity without parsing the
            human-readable message.

    Example::

        raise LocalModeUnsupportedError(feature="test_provider")
    """

    def __init__(self, *, feature: str) -> None:
        self.feature = feature
        super().__init__(
            f"This action ({feature}) is unavailable in Local mode. "
            "Start the daemon (`voicegw start`) to enable it."
        )


__all__ = ["LocalModeUnsupportedError"]
