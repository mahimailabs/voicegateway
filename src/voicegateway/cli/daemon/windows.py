"""Windows Scheduled Task backend for the v0.1.0 daemon.

Best-effort. Per design.md \xa77 (out of scope) the official Windows
path is WSL on top of the Linux backend; this module exists so a
plain-Windows install does not error out, not because it is the
recommended setup.

Wraps ``schtasks.exe`` for lifecycle (Create / Delete / Run / End /
Query) and falls back to a Start Menu Startup-folder ``.lnk`` if
schtasks fails. Logs come from the same per-user platformdirs path
voicegw serve writes to (``%LOCALAPPDATA%\\voicegateway\\Logs``);
schtasks does not capture stdout itself.

Caveats relative to the macOS / Linux backends:

- No native ``KeepAlive`` equivalent: a Scheduled Task with
  ``/SC ONLOGON`` runs at logon, not on crash. Operators relying
  on auto-restart should use WSL.
- ``status()['pid']`` is always None: schtasks does not expose the
  managed process PID. ``running`` is sourced from the
  ``Status:`` field of ``schtasks /Query``.
- Path quoting follows Windows conventions; tests run on macOS
  but never invoke schtasks for real.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from platformdirs import user_log_dir

SERVICE_NAME = "VoiceGateway"


class WindowsBackend:
    """Scheduled Task backend with Startup-folder shortcut fallback.

    Constructor takes an optional ``service_name`` for tests; the
    default ``VoiceGateway`` is used as both the Task name and the
    .lnk filename in the Startup folder.
    """

    def __init__(self, *, service_name: str = SERVICE_NAME) -> None:
        self._service_name = service_name
        home = Path.home()
        self._home = home

        # Windows per-user Startup folder. Lives in roaming AppData
        # so it follows the user across machines bound to the same
        # AD account; this matches what the Settings UI does when a
        # user marks "Start at sign-in" for a third-party app.
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

    def install(self) -> None:
        """Try schtasks first, fall back to Startup-folder shortcut.

        Raises ``RuntimeError`` only when BOTH paths fail. Either
        path on its own is enough to satisfy the daemon-at-login
        contract; we prefer schtasks because it surfaces in the
        Task Scheduler UI and supports the lifecycle ops, but the
        shortcut fallback covers locked-down corporate boxes
        where schtasks is restricted.
        """
        executable = shutil.which("voicegw") or shutil.which("voicegw.exe")
        if executable is None:
            raise RuntimeError(
                "Could not find 'voicegw' on PATH. Make sure pipx install "
                "ran successfully and ~\\.local\\bin (or the pipx user-bin "
                "directory) is on your PATH."
            )

        result = self._schtasks(
            "/Create",
            "/SC",
            "ONLOGON",
            "/TN",
            self._task_name,
            "/TR",
            f'"{executable}" serve',
            "/RL",
            "LIMITED",
            "/F",
        )
        if result.returncode == 0:
            return

        # Fallback: drop a shortcut into the Startup folder via
        # PowerShell. If THAT also fails we have nothing left;
        # raise with both error trails so the operator can see why.
        try:
            self._install_startup_shortcut(executable)
        except RuntimeError as fallback_error:
            raise RuntimeError(
                "Daemon registration failed via both schtasks "
                f"({result.returncode}: {result.stderr.strip()}) and the "
                f"Startup-folder fallback ({fallback_error})."
            ) from fallback_error

    def uninstall(self) -> None:
        """Best-effort: remove BOTH the Scheduled Task and the
        Startup-folder shortcut. Either may already be absent;
        non-zero exit is swallowed.
        """
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
        # schtasks /End returns non-zero when the task is not
        # currently running. That is the right "I asked it to stop
        # and it's stopped" outcome, not an error.
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
        """Tail the per-user log file voicegw serve writes to.

        schtasks does not capture stdout itself; voicegw serve writes
        to ``%LOCALAPPDATA%\\voicegateway\\Logs\\serve.log`` directly.
        Returns empty when the file does not exist (typical on a
        fresh install before serve has run).
        """
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
        """Single subprocess seam for schtasks. Tests monkeypatch
        this module's ``subprocess.run``.
        """
        return subprocess.run(
            ["schtasks", *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def _install_startup_shortcut(self, executable: str) -> None:
        """Fallback path: drop a .lnk into the Startup folder via
        PowerShell + WScript.Shell.

        Raises RuntimeError on PowerShell failure so the caller's
        chain in ``install()`` can attribute the message.
        """
        self._startup_dir.mkdir(parents=True, exist_ok=True)

        ps_script = (
            "$WshShell = New-Object -ComObject WScript.Shell;"
            f'$Shortcut = $WshShell.CreateShortcut("{self._shortcut_path}");'
            f'$Shortcut.TargetPath = "{executable}";'
            '$Shortcut.Arguments = "serve";'
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
