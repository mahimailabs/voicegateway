"""Linux systemd user-unit backend for the v0.1.0 daemon.

Wraps ``systemctl --user`` against a per-user unit file at
``~/.config/systemd/user/voicegateway.service``. The unit file is
rendered from ``templates/systemd.service`` via Python's
``string.Template`` per design.md hard rule.

Logs flow through journald; ``logs()`` calls ``journalctl --user-unit
voicegateway`` rather than tailing files. This matches the
design.md decision (and avoids the file-rotation question that the
LaunchAgent backend has to live with).

WSL transparently uses this backend: ``sys.platform`` is ``linux``
inside WSL, the DaemonManager selector routes there, and modern WSL
distributions ship a working ``systemctl --user``. Bare-Windows
support is the Scheduled Task backend (``windows.py``).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from string import Template
from typing import Any

SERVICE_NAME = "voicegateway"
_UNIT_FILENAME = f"{SERVICE_NAME}.service"
_TEMPLATE_PATH = Path(__file__).parent / "templates" / "systemd.service"


class LinuxBackend:
    """systemd user-unit daemon backend.

    Constructor takes an optional ``service_name`` for tests; the
    default ``voicegateway`` matches the unit filename and is what
    every doctor and lifecycle command keys off.
    """

    def __init__(self, *, service_name: str = SERVICE_NAME) -> None:
        self._service_name = service_name

        home = Path.home()
        self._home = home
        self._unit_dir = home / ".config" / "systemd" / "user"
        self._unit_path = self._unit_dir / f"{self._service_name}.service"

    # ---- DaemonBackend Protocol -------------------------------------------

    def install(self) -> None:
        """Render unit, write it, daemon-reload, enable, start.

        Idempotent: re-running on an already-installed machine
        refreshes the unit file (catches voicegw upgrades that move
        the binary path) and ensures the unit is enabled and active.
        """
        executable = shutil.which("voicegw")
        if executable is None:
            raise RuntimeError(
                "Could not find 'voicegw' on PATH. Make sure pipx install "
                "ran successfully and ~/.local/bin is on your PATH "
                "(open a new shell after `pipx ensurepath`)."
            )

        self._unit_dir.mkdir(parents=True, exist_ok=True)
        rendered = self._render_unit(executable_path=executable)
        self._unit_path.write_text(rendered, encoding="utf-8")
        self._unit_path.chmod(0o644)

        # Pick up the new (or refreshed) unit definition.
        self._systemctl("daemon-reload", check=True)
        # Enable so the unit auto-starts at next login (and starts now).
        self._systemctl("enable", "--now", self._unit_name(), check=True)

    def uninstall(self) -> None:
        """Stop, disable, remove unit, daemon-reload.

        Per design.md decision 5, only the registration goes; config
        and the SQLite DB are preserved. Idempotent: ``systemctl stop``
        and ``disable`` exit non-zero on missing units, which we
        swallow.
        """
        # Best-effort stop + disable. Either may exit non-zero if the
        # unit was never enabled or the systemd state is already in
        # the target shape; ignore and continue.
        self._systemctl("disable", "--now", self._unit_name())
        self._unit_path.unlink(missing_ok=True)
        self._systemctl("daemon-reload")

    def start(self) -> None:
        """Bring the daemon up. Requires install() to have run first."""
        if not self._unit_path.exists():
            raise RuntimeError(
                f"Unit file missing at {self._unit_path}. "
                "Run `voicegw onboard --install-daemon` first."
            )
        self._systemctl("start", self._unit_name(), check=True)

    def stop(self) -> None:
        """Bring the daemon down. No-op if not registered."""
        if not self._unit_path.exists():
            return
        self._systemctl("stop", self._unit_name(), check=True)

    def restart(self) -> None:
        """``systemctl restart``: stop + start in one round-trip."""
        if not self._unit_path.exists():
            raise RuntimeError(
                f"Unit file missing at {self._unit_path}. "
                "Run `voicegw onboard --install-daemon` first."
            )
        self._systemctl("restart", self._unit_name(), check=True)

    def status(self) -> dict[str, Any]:
        """Read-only state dump. Never raises."""
        show = self._systemctl_show()
        load_state = show.get("LoadState", "")
        active_state = show.get("ActiveState", "")
        main_pid_raw = show.get("MainPID", "0")

        try:
            main_pid_int = int(main_pid_raw)
        except (TypeError, ValueError):
            main_pid_int = 0

        registered = load_state == "loaded" and self._unit_path.exists()
        running = active_state == "active"
        pid = main_pid_int if main_pid_int > 0 else None

        return {
            "registered": registered,
            "running": running,
            "pid": pid,
            "service_name": self._service_name,
            "unit_path": str(self._unit_path),
            "active_state": active_state,
            "load_state": load_state,
        }

    def logs(self, *, tail: int = 100) -> str:
        """Return the last ``tail`` lines from journald for this unit."""
        result = subprocess.run(
            [
                "journalctl",
                "--user-unit",
                self._unit_name(),
                "-n",
                str(tail),
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            # journalctl exits non-zero when the journal is unreadable
            # or no entries match. Return empty rather than raising;
            # a missing journal is "no logs yet", not a backend error.
            return ""
        return result.stdout

    # ---- Internals ---------------------------------------------------------

    def _unit_name(self) -> str:
        return f"{self._service_name}.service"

    def _render_unit(self, *, executable_path: str) -> str:
        tmpl = Template(_TEMPLATE_PATH.read_text())
        return tmpl.substitute(
            executable_path=executable_path,
            working_directory=str(self._home),
        )

    def _systemctl(
        self, *args: str, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        """Single subprocess seam for systemctl --user. Tests
        monkeypatch this module's ``subprocess.run``.
        """
        result = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"systemctl --user {' '.join(args)} failed "
                f"({result.returncode}): {result.stderr.strip()}"
            )
        return result

    def _systemctl_show(self) -> dict[str, str]:
        """Run ``systemctl --user show <unit>`` and parse the
        ``Key=Value`` output. Returns an empty dict on failure
        so ``status()`` can still report a sensible shape.
        """
        result = self._systemctl("show", self._unit_name())
        if result.returncode != 0 or not result.stdout:
            return {}
        out: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
        return out
