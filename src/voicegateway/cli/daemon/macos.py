"""macOS LaunchAgent backend for the daemon."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from string import Template
from typing import Any

from platformdirs import user_log_dir

SERVICE_NAME = "ai.openrtc.voicegateway"
_PLIST_FILENAME = f"{SERVICE_NAME}.plist"
_TEMPLATE_PATH = Path(__file__).parent / "templates" / "launchagent.plist"


_LAUNCHCTL_ALREADY_LOADED_RC = 17


class MacOSBackend:
    """LaunchAgent-based daemon backend."""

    def __init__(self, *, service_name: str = SERVICE_NAME) -> None:
        self._service_name = service_name

        home = Path.home()
        self._home = home
        self._launch_agents_dir = home / "Library" / "LaunchAgents"
        self._plist_path = self._launch_agents_dir / _PLIST_FILENAME

        log_dir = Path(user_log_dir("voicegateway"))
        self._log_dir = log_dir
        self._stdout_log = log_dir / "serve.log"
        self._stderr_log = log_dir / "serve.err.log"

        self._uid = os.getuid()
        self._domain_target = f"gui/{self._uid}"
        self._service_target = f"{self._domain_target}/{self._service_name}"

    # ---- DaemonBackend Protocol -------------------------------------------

    def install(self) -> None:
        """Render plist, write it, bootstrap. Idempotent on re-run."""
        executable = shutil.which("voicegw")
        if executable is None:
            raise RuntimeError(
                "Could not find 'voicegw' on PATH. Make sure pipx install "
                "ran successfully and ~/.local/bin is on your PATH "
                "(open a new shell after `pipx ensurepath`)."
            )

        self._launch_agents_dir.mkdir(parents=True, exist_ok=True)
        self._log_dir.mkdir(parents=True, exist_ok=True)

        rendered = self._render_plist(executable_path=executable)
        self._plist_path.write_text(rendered, encoding="utf-8")
        self._plist_path.chmod(0o644)

        result = self._run_launchctl(
            "bootstrap", self._domain_target, str(self._plist_path)
        )
        if result.returncode not in (0, _LAUNCHCTL_ALREADY_LOADED_RC):
            raise RuntimeError(
                f"launchctl bootstrap failed ({result.returncode}): "
                f"{result.stderr.strip()}"
            )

    def uninstall(self) -> None:
        """bootout + delete plist. Per design.md decision 5, config"""
        self._run_launchctl("bootout", self._service_target)
        self._plist_path.unlink(missing_ok=True)

    def start(self) -> None:
        """Bring the daemon up. Bootstraps first if not already loaded."""
        if not self._is_registered():
            if not self._plist_path.exists():
                raise RuntimeError(
                    f"Daemon plist missing at {self._plist_path}. "
                    "Run `voicegw onboard --install-daemon` first."
                )
            result = self._run_launchctl(
                "bootstrap", self._domain_target, str(self._plist_path)
            )
            if result.returncode not in (0, _LAUNCHCTL_ALREADY_LOADED_RC):
                raise RuntimeError(
                    f"launchctl bootstrap failed ({result.returncode}): "
                    f"{result.stderr.strip()}"
                )
            return

        result = self._run_launchctl("kickstart", self._service_target)
        if result.returncode != 0:
            raise RuntimeError(
                f"launchctl kickstart failed ({result.returncode}): "
                f"{result.stderr.strip()}"
            )

    def stop(self) -> None:
        """Bring the daemon down. No-op if not registered."""
        if not self._is_registered():
            return
        result = self._run_launchctl("bootout", self._service_target)
        if result.returncode != 0:
            raise RuntimeError(
                f"launchctl bootout failed ({result.returncode}): "
                f"{result.stderr.strip()}"
            )

    def restart(self) -> None:
        """``kickstart -k``: kills the running process and respawns."""
        if not self._is_registered():
            self.start()
            return
        result = self._run_launchctl("kickstart", "-k", self._service_target)
        if result.returncode != 0:
            raise RuntimeError(
                f"launchctl kickstart -k failed ({result.returncode}): "
                f"{result.stderr.strip()}"
            )

    def status(self) -> dict[str, Any]:
        """Read-only state dump. Never raises."""
        return {
            "registered": self._is_registered(),
            "running": self._is_running(),
            "pid": self._pid(),
            "service_name": self._service_name,
            "plist_path": str(self._plist_path),
        }

    def logs(self, *, tail: int = 100) -> str:
        """Return the last ``tail`` lines of stdout (with stderr appended)."""
        out = self._tail_file(self._stdout_log, tail)
        err = self._tail_file(self._stderr_log, tail)
        if err:
            sep = "\n" if out and not out.endswith("\n") else ""
            return f"{out}{sep}--- stderr ---\n{err}"
        return out

    # ---- Internals ---------------------------------------------------------

    def _render_plist(self, *, executable_path: str) -> str:
        tmpl = Template(_TEMPLATE_PATH.read_text())
        return tmpl.substitute(
            service_name=self._service_name,
            executable_path=executable_path,
            stdout_log_path=str(self._stdout_log),
            stderr_log_path=str(self._stderr_log),
            working_directory=str(self._home),
        )

    def _run_launchctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Single subprocess seam. Tests monkeypatch this module's"""
        return subprocess.run(
            ["launchctl", *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def _print_output(self) -> str | None:
        """``launchctl print``; returns stdout when registered, None otherwise."""
        result = self._run_launchctl("print", self._service_target)
        if result.returncode != 0:
            return None
        return result.stdout

    def _is_registered(self) -> bool:
        return self._print_output() is not None

    def _is_running(self) -> bool:
        out = self._print_output()
        if out is None:
            return False
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("state = "):
                return stripped.removeprefix("state = ").rstrip(",;") == "running"
        return False

    def _pid(self) -> int | None:
        out = self._print_output()
        if out is None:
            return None
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("pid = "):
                value = stripped.removeprefix("pid = ").rstrip(",;").strip()
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    def _tail_file(self, path: Path, tail: int) -> str:
        if not path.exists():
            return ""
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return ""
        return "".join(lines[-tail:])
