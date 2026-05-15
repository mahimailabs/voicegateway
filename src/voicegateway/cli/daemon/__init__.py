"""Daemon package: Protocol contract + facade + per-OS backends.

Layout:

- :mod:`voicegateway.cli.daemon.base_daemon` defines the
  :class:`DaemonBackend` Protocol every OS backend satisfies.
- :mod:`voicegateway.cli.daemon.manager` hosts :class:`DaemonManager`
  (the facade) plus the platform selector that lazy-imports the right
  backend module for the current OS.
- ``linux_daemon.py`` / ``macos_daemon.py`` / ``windows_daemon.py``
  host the three concrete backends; ``templates/`` ships the OS-specific
  service definitions (LaunchAgent plist, systemd unit).
"""

from __future__ import annotations

from voicegateway.cli.daemon.base_daemon import DaemonBackend
from voicegateway.cli.daemon.manager import DaemonManager

__all__ = ["DaemonBackend", "DaemonManager"]
