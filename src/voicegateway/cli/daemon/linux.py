"""Linux systemd user-unit backend for the daemon."""

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
    """systemd user-unit daemon backend."""

    def __init__(self, *, service_name: str = SERVICE_NAME) -> None:
        self._service_name = service_name

        home = Path.home()
        self._home = home
        self._unit_dir = home / ".config" / "systemd" / "user"
        self._unit_path = self._unit_dir / f"{self._service_name}.service"

    # ---- DaemonBackend Protocol -------------------------------------------

    def install(self) -> None:
        """Render unit, write it, daemon-reload, enable, start."""
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
        """Stop, disable, remove unit, daemon-reload."""
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
        """Single subprocess seam for systemctl --user. Tests"""
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
        """Run ``systemctl --user show <unit>`` and parse the"""
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
