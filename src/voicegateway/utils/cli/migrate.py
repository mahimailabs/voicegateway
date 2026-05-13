"""Helpers for ``voicegateway.cli.migrate``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rich.table import Table

from voicegateway.cli._app import console
from voicegateway.utils.cli._shared import _config_home


@dataclass
class MigrationReport:
    """Structured outcome of a migrate run."""

    config_path: Path
    db_path: Path
    config_present: bool = False
    config_parseable: bool = False
    db_present: bool = False
    db_readable: bool = False
    managed_provider_count: int = 0
    keys_decrypt: bool = False
    keys_failed_to_decrypt: list[str] = field(default_factory=list)
    daemon_registered: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def has_v005_install(self) -> bool:
        """True when at least the config file exists.

        v0.0.5 always wrote a yaml; the SQLite db may be absent
        if cost-tracking was disabled. config_present is the
        load-bearing detection signal.
        """
        return self.config_present


def _atomic_write_text(target: Path, content: str) -> None:
    """Stage to ``target.with_suffix('.tmp')``, fsync, then atomic-rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + ".tmp")

    try:
        with open(staging, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            import os as _os

            _os.fsync(f.fileno())
        staging.replace(target)
    except Exception:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _build_report(home: Path | None = None) -> MigrationReport:
    """Inspect the canonical config home and return a structured report."""
    home = home if home is not None else _config_home()
    report = MigrationReport(
        config_path=home / "voicegw.yaml",
        db_path=home / "voicegw.db",
    )

    _check_config(report)
    _check_db(report)
    _check_managed_providers(report)
    _check_daemon_registration(report)
    return report


def _check_config(report: MigrationReport) -> None:
    if not report.config_path.exists():
        report.notes.append(
            f"No voicegw.yaml at {report.config_path}; "
            "run `voicegw onboard` to start fresh."
        )
        return
    report.config_present = True
    try:
        import yaml

        yaml.safe_load(report.config_path.read_text())
        report.config_parseable = True
    except Exception as exc:  # noqa: BLE001
        report.notes.append(
            f"voicegw.yaml at {report.config_path} did not parse: {exc}. "
            "Inspect the file or run `voicegw init` to scaffold a fresh "
            "config (existing data preserved if you back the file up first)."
        )


def _check_db(report: MigrationReport) -> None:
    if not report.db_path.exists():
        report.notes.append(
            "No voicegw.db on disk. v0.0.5 only writes the SQLite database "
            "when cost_tracking is enabled; this is fine if you intentionally "
            "ran with cost-tracking off."
        )
        return
    report.db_present = True
    try:
        import sqlite3

        conn = sqlite3.connect(str(report.db_path))
        try:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cur.fetchall()}
            expected = {"managed_providers", "sessions", "requests"}
            missing = expected - tables
            if missing:
                report.notes.append(
                    f"voicegw.db is missing expected tables: "
                    f"{sorted(missing)}. Run `voicegw doctor` for deeper "
                    "diagnostics."
                )
                return
            report.db_readable = True
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        report.notes.append(
            f"voicegw.db at {report.db_path} did not open: {exc}. "
            "Check file permissions or a corrupt SQLite file."
        )


def _check_managed_providers(report: MigrationReport) -> None:
    """Best-effort: verify Fernet-encrypted keys still decrypt."""
    if not report.db_readable:
        return
    try:
        import sqlite3

        conn = sqlite3.connect(str(report.db_path))
        try:
            cur = conn.execute(
                "SELECT provider_id, api_key_encrypted FROM managed_providers"
            )
            rows = cur.fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return

    report.managed_provider_count = len(rows)
    if not rows:
        report.keys_decrypt = True  # nothing to verify, trivially "ok"
        return

    try:
        from voicegateway.core.crypto import decrypt
    except ImportError:
        report.notes.append(
            "voicegateway.core.crypto not importable; skipping key "
            "verification. Re-install with the matching extras."
        )
        return

    failures: list[str] = []
    for provider_id, encrypted in rows:
        try:
            decrypt(encrypted or "")
        except Exception:  # noqa: BLE001
            failures.append(provider_id)

    report.keys_failed_to_decrypt = failures
    report.keys_decrypt = not failures
    if failures:
        report.notes.append(
            f"{len(failures)} managed provider key(s) did not decrypt under "
            "the current VOICEGW_SECRET. Set VOICEGW_SECRET to the value the "
            "v0.0.5 install used, or set VOICEGW_SECRET_FALLBACK to it and "
            "run `voicegw rotate-secret` to re-encrypt under a new primary."
        )


def _check_daemon_registration(report: MigrationReport) -> None:
    """Read the platform daemon-status payload (best-effort)."""
    try:
        from voicegateway.cli.daemon import DaemonManager

        info = DaemonManager().status()
        report.daemon_registered = bool(info.get("registered"))
    except Exception:  # noqa: BLE001
        report.daemon_registered = False


def _render(report: MigrationReport) -> None:
    """Print the structured summary."""
    table = Table(title="VoiceGateway migration", show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column()

    table.add_row(
        "Config",
        f"[green]found[/green] at {report.config_path}"
        if report.config_present
        else "[yellow]not found[/yellow]",
    )
    table.add_row(
        "Config parseable",
        "[green]yes[/green]" if report.config_parseable else "[red]no[/red]",
    )
    table.add_row(
        "SQLite db",
        f"[green]found[/green] at {report.db_path}"
        if report.db_present
        else "[dim]not found (cost-tracking disabled?)[/dim]",
    )
    if report.db_present:
        table.add_row(
            "Db readable",
            "[green]yes[/green]" if report.db_readable else "[red]no[/red]",
        )
    if report.managed_provider_count:
        table.add_row(
            "Managed providers",
            f"{report.managed_provider_count} row(s); keys decrypt: "
            + ("[green]yes[/green]" if report.keys_decrypt else "[red]no[/red]"),
        )
    table.add_row(
        "Daemon registered",
        "[green]yes[/green]" if report.daemon_registered else "[yellow]no[/yellow]",
    )

    console.print(table)

    for note in report.notes:
        console.print(f"\n[yellow]Note:[/yellow] {note}")

    console.print()
    if not report.has_v005_install:
        console.print(
            "[bold]No v0.0.5 install to migrate.[/bold] "
            "Run [cyan]voicegw onboard[/cyan] to set one up."
        )
        return

    if not report.daemon_registered:
        console.print(
            "[bold]Recommended next step:[/bold] register the daemon so the "
            "gateway auto-starts at login.\n"
            "  [cyan]voicegw onboard --install-daemon[/cyan]"
        )
    else:
        console.print(
            "[bold]Migration complete.[/bold] Daemon is registered; run "
            "[cyan]voicegw status[/cyan] to verify it's running."
        )

    console.print(
        "\n[dim]This command is read-only: no files were written; "
        "your v0.0.5 install is unchanged.[/dim]"
    )
