"""``ProviderRow`` widget for the Providers tab."""

from __future__ import annotations

from typing import Any

from textual.widgets import Static

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
        """Replace the underlying dict and re-render the row."""
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
    """Return the plain-ASCII indicator for ``status``."""
    return _STATUS_DISPLAY[_normalise_status(status)][0]


def _normalise_status(status: Any) -> str:
    if isinstance(status, str) and status in _STATUS_DISPLAY:
        return status
    return "untested"


__all__ = ["ProviderRow", "format_status"]
