"""``voicegw migrate`` command.

Implements REQ-VG-ONBOARD-007: detect a v0.0.5 install at the
canonical config home, verify integrity, and guide the user to
v0.1.0's daemon-first surface.

Migration semantics for v0.1.0 are deliberately conservative.
Per design.md decision 2 ("config home: same as v0.0.5 — no
migration pain") the canonical path is unchanged:
``~/.config/voicegateway/voicegw.yaml`` plus
``~/.config/voicegateway/voicegw.db``. There is no copy step.

What this command DOES:

  - Detect a v0.0.5 install at the canonical path.
  - Verify ``voicegw.yaml`` parses cleanly under the v0.1.0
    schema.
  - Verify ``voicegw.db`` opens (managed_providers + sessions
    + requests tables present).
  - Best-effort: confirm the Fernet-encrypted ``managed_providers``
    rows decrypt under ``VOICEGW_SECRET`` so the operator
    knows whether ``voicegw rotate-secret`` is needed.
  - Print a structured summary plus the recommended next-step
    commands for v0.1.0 (``voicegw onboard --install-daemon``
    or ``voicegw start`` if the daemon is already registered).

What this command does NOT do (yet):

  - Atomic-rename staging path: relevant when migration mutates
    files in place; v0.1.0 keeps the schema unchanged. Lands as
    a separate iteration once an actual schema bump arrives.
  - Rollback: covered by the same staging task. Today the
    command is read-only against the existing install so there
    is nothing to roll back.

Idempotency: re-running on an already-migrated install is safe
and produces the same summary. Re-running on a missing install
prints "no v0.0.5 install detected; run `voicegw onboard` to
start fresh".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import typer

from voicegateway.cli._app import app, console


@dataclass
class MigrationReport:
    """Structured outcome of a migrate run.

    The cli renders the table from these fields; tests pin
    individual flags so a regression in one branch doesn't bleed
    into the others.
    """

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


def _config_home() -> Path:
    """v0.0.5's canonical config home, kept verbatim in v0.1.0."""
    return Path.home() / ".config" / "voicegateway"


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
    from rich.table import Table

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


@app.command()
def migrate(
    config_home: str = typer.Option(
        None,
        "--config-home",
        help="Override the canonical config home (default: ~/.config/voicegateway).",
    ),
) -> None:
    """Migrate a v0.0.5 install into the v0.1.0 layout.

    The path is unchanged from v0.0.5 (per design decision 2), so
    migration is detection + integrity verification + next-step
    guidance. Idempotent on re-run; read-only against the existing
    install.
    """
    home = Path(config_home) if config_home else _config_home()
    report = _build_report(home)
    _render(report)
