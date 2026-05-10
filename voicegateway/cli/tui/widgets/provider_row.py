"""``ProviderRow`` widget for the Providers tab (REQ-VG-TUI-005).

One row per configured provider. Renders a status indicator, the
provider id, the provider type, and the owning project so the
Providers tab list reads like:

    [ok]    openai             openai      tony-pizza
    [fail]  deepgram-broken    deepgram    default
    [?]     cartesia           cartesia    (global)

Status indicators are plain ASCII (``[ok]`` / ``[fail]`` / ``[?]``)
so the row remains unambiguous on terminals that drop styling. A
CSS class (``status-ok`` / ``status-fail`` / ``status-untested``)
is set on the widget so Phase 8's TCSS pass can drop the brand
green / red colors without touching the Python.

Expected provider dict shape (matches the daemon's
``GET /v1/providers`` response and LocalClient's
``list_managed_providers`` mirror):

    {
        "provider_id": str,
        "provider_type": str,         # e.g. "openai"
        "project": str | None,         # None for global / pre-v0.0.5 rows
        "status": "ok" | "fail" | "untested" | None,
        "last_tested_at": str | None,  # ISO 8601, optional
    }
"""

from __future__ import annotations

from typing import Any

from textual.widgets import Static

#: Status -> ``(indicator, css_class)``. Anything outside this
#: mapping (None, empty string, "untested", an unknown value) renders
#: as the untested form so the column alignment stays predictable.
_STATUS_DISPLAY: dict[str, tuple[str, str]] = {
    "ok": ("[ok]  ", "status-ok"),
    "fail": ("[fail]", "status-fail"),
    "untested": ("[?]   ", "status-untested"),
}


class ProviderRow(Static):
    """One row in the Providers tab list."""

    DEFAULT_CSS = """
    ProviderRow {
        height: 1;
    }
    ProviderRow:focus {
        background: $accent 30%;
    }
    /* Status colors are placeholder; Phase 8 TCSS pass refines. */
    ProviderRow.status-ok {
        color: $success;
    }
    ProviderRow.status-fail {
        color: $error;
    }
    ProviderRow.status-untested {
        color: $text-muted;
    }
    """

    can_focus = True

    def __init__(self, provider: dict[str, Any], **kwargs: Any) -> None:
        self.provider = provider
        super().__init__(self._format(), **kwargs)
        self.add_class(self._status_class())

    def update_provider(self, provider: dict[str, Any]) -> None:
        """Replace the underlying dict and re-render the row.

        The Providers screen calls this after a successful test or
        on poll-driven status update so the indicator + colour flip
        without re-mounting the row (focus + scroll state survive).
        """
        old_class = self._status_class()
        self.provider = provider
        self.update(self._format())
        new_class = self._status_class()
        if old_class != new_class:
            self.remove_class(old_class)
            self.add_class(new_class)

    @property
    def provider_id(self) -> str:
        """Storage-canonical id used by the Providers screen's ``t``
        shortcut to drive ``MetricsClient.test_provider``."""
        return str(self.provider.get("provider_id") or "")

    @property
    def status(self) -> str:
        return _normalise_status(self.provider.get("status"))

    # -- Formatting --------------------------------------------------

    def _status_class(self) -> str:
        return _STATUS_DISPLAY[self.status][1]

    def _format(self) -> str:
        indicator = format_status(self.provider.get("status"))
        provider_id = str(self.provider.get("provider_id") or "?")
        provider_type = str(
            self.provider.get("provider_type") or self.provider.get("type") or ""
        )
        project_value = self.provider.get("project")
        project = str(project_value) if project_value else "(global)"
        return f"{indicator}  {provider_id:<20}  {provider_type:<12}  {project}"


def format_status(status: Any) -> str:
    """Return the plain-ASCII indicator for ``status``.

    Accepts the documented ``ok`` / ``fail`` / ``untested`` strings
    plus ``None`` and anything else; falls back to the untested
    form so a malformed daemon response never breaks row alignment.
    """
    return _STATUS_DISPLAY[_normalise_status(status)][0]


def _normalise_status(status: Any) -> str:
    if isinstance(status, str) and status in _STATUS_DISPLAY:
        return status
    return "untested"


__all__ = ["ProviderRow", "format_status"]
