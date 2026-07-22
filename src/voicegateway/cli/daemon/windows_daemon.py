"""Windows Scheduled Task backend for the daemon."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from platformdirs import user_log_dir

SERVICE_NAME = "VoiceGateway"


class WindowsBackend:
    """Scheduled Task backend with Startup-folder shortcut fallback."""

    def __init__(self, *, service_name: str = SERVICE_NAME) -> None:
        self._service_name = service_name
        home = Path.home()
        self._home = home

        self._startup_dir = (
            home
            / "AppData"
            / "Roaming"
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
        )
        self._shortcut_path = self._startup_dir / f"{self._service_name}.lnk"
        self._task_name = self._service_name
        self._log_dir = Path(user_log_dir("voicegateway"))
        self._stdout_log = self._log_dir / "serve.log"

    # ---- DaemonBackend Protocol -------------------------------------------

    def install(self, config_path: str | None = None) -> None:
        """Try schtasks first, fall back to Startup-folder shortcut.

        When ``config_path`` is given the task runs ``voicegw serve -c
        <config_path>`` so the daemon serves the exact file the operator
        onboarded against; otherwise ``serve`` uses the config search path.
        """
        executable = shutil.which("voicegw") or shutil.which("voicegw.exe")
        if executable is None:
            raise RuntimeError(
                "Could not find 'voicegw' on PATH. Make sure pipx install "
                "ran successfully and ~\\.local\\bin (or the pipx user-bin "
                "directory) is on your PATH."
            )

        task_run = f'"{executable}" serve'
        if config_path:
            task_run += f' -c "{config_path}"'

        result = self._schtasks(
            "/Create",
            "/SC",
            "ONLOGON",
            "/TN",
            self._task_name,
            "/TR",
            task_run,
            "/RL",
            "LIMITED",
            "/F",
        )
        if result.returncode == 0:
            return

        try:
            self._install_startup_shortcut(executable, config_path)
        except RuntimeError as fallback_error:
            raise RuntimeError(
                "Daemon registration failed via both schtasks "
                f"({result.returncode}: {result.stderr.strip()}) and the "
                f"Startup-folder fallback ({fallback_error})."
            ) from fallback_error

    def uninstall(self) -> None:
        """Best-effort: remove BOTH the Scheduled Task and the"""
        self._schtasks("/Delete", "/TN", self._task_name, "/F")
        self._shortcut_path.unlink(missing_ok=True)

    def start(self) -> None:
        """``schtasks /Run`` the registered task."""
        result = self._schtasks("/Run", "/TN", self._task_name)
        if result.returncode != 0:
            raise RuntimeError(
                f"schtasks /Run failed ({result.returncode}): {result.stderr.strip()}"
            )

    def stop(self) -> None:
        """``schtasks /End``. No-op-equivalent if the task isn't running."""

        self._schtasks("/End", "/TN", self._task_name)

    def restart(self) -> None:
        self.stop()
        self.start()

    def status(self) -> dict[str, Any]:
        """Read-only state. Never raises."""
        result = self._schtasks("/Query", "/TN", self._task_name, "/V", "/FO", "LIST")
        registered = result.returncode == 0
        running = False
        if registered:
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith("Status:"):
                    value = stripped.removeprefix("Status:").strip()
                    running = value == "Running"
                    break
        return {
            "registered": registered,
            "running": running,
            "pid": None,  # schtasks does not expose the managed PID
            "service_name": self._service_name,
            "task_name": self._task_name,
            "shortcut_path": str(self._shortcut_path),
        }

    def logs(self, *, tail: int = 100) -> str:
        """Tail the per-user log file voicegw serve writes to."""
        if not self._stdout_log.exists():
            return ""
        try:
            with open(self._stdout_log, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return ""
        return "".join(lines[-tail:])

    # ---- Internals ---------------------------------------------------------

    def _schtasks(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Single subprocess seam for schtasks. Tests monkeypatch"""
        return subprocess.run(
            ["schtasks", *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def _install_startup_shortcut(
        self, executable: str, config_path: str | None = None
    ) -> None:
        """Fallback path: drop a .lnk into the Startup folder via"""
        self._startup_dir.mkdir(parents=True, exist_ok=True)

        arguments = "serve"
        if config_path:
            arguments = f'serve -c "{config_path}"'
        # Escape embedded quotes for the PowerShell double-quoted string literal.
        ps_arguments = arguments.replace('"', '`"')

        ps_script = (
            "$WshShell = New-Object -ComObject WScript.Shell;"
            f'$Shortcut = $WshShell.CreateShortcut("{self._shortcut_path}");'
            f'$Shortcut.TargetPath = "{executable}";'
            f'$Shortcut.Arguments = "{ps_arguments}";'
            f'$Shortcut.WorkingDirectory = "{self._home}";'
            "$Shortcut.Save()"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"PowerShell exit {result.returncode}: {result.stderr.strip()}"
            )
