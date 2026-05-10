"""Tests for ``voicegw migrate``.

Per design decision 2 the v0.1.0 config home matches v0.0.5, so
migration is read-only detection + integrity verification + next-
step guidance. Tests build sample v0.0.5 install fixtures under
tmp_path and run the command against ``--config-home``.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest
import yaml
from typer.testing import CliRunner

from voicegateway.cli import app

runner = CliRunner()


def _make_v005_yaml(home, providers=None):
    """Drop a minimal v0.0.5-shaped voicegw.yaml at <home>/voicegw.yaml."""
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "voicegw.yaml"
    cfg.write_text(
        yaml.dump(
            {
                "providers": providers or {"openai": {"api_key": "sk-test"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "cost_tracking": {"enabled": True},
            }
        )
    )
    return cfg


def _make_v005_db(home, *, with_managed_providers=()):
    """Drop a minimal v0.0.5-shaped voicegw.db at <home>/voicegw.db.

    ``with_managed_providers`` is a list of (provider_id,
    api_key_encrypted) tuples to seed in managed_providers.
    """
    home.mkdir(parents=True, exist_ok=True)
    db = home / "voicegw.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE managed_providers (provider_id TEXT, api_key_encrypted TEXT)"
    )
    conn.execute("CREATE TABLE sessions (id TEXT)")
    conn.execute("CREATE TABLE requests (id TEXT)")
    for pid, key in with_managed_providers:
        conn.execute("INSERT INTO managed_providers VALUES (?, ?)", (pid, key))
    conn.commit()
    conn.close()
    return db


@pytest.fixture(autouse=True)
def _stub_daemon_manager(monkeypatch):
    """Replace DaemonManager with a fake reporting registered=False
    so migrate's daemon-registration check is deterministic.
    """
    fake = MagicMock()
    fake.status.return_value = {"registered": False, "running": False, "pid": None}
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager",
        MagicMock(return_value=fake),
    )


# ---------------------------------------------------------------------------
# No install detected
# ---------------------------------------------------------------------------


def test_migrate_when_no_install_present(tmp_path):
    """No yaml on disk -> tells the user to run voicegw onboard."""
    result = runner.invoke(app, ["migrate", "--config-home", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "No v0.0.5 install" in result.output
    assert "voicegw onboard" in result.output


# ---------------------------------------------------------------------------
# Happy path: yaml + db both present and clean
# ---------------------------------------------------------------------------


def test_migrate_detects_clean_install_no_keys(tmp_path):
    """v0.0.5 yaml + db with no managed_providers rows: clean detection,
    daemon-not-registered prompts the install-daemon next step.
    """
    home = tmp_path / "voicegw"
    _make_v005_yaml(home)
    _make_v005_db(home)

    result = runner.invoke(app, ["migrate", "--config-home", str(home)])
    assert result.exit_code == 0, result.output

    out = result.output
    assert "found" in out  # config + db
    assert "voicegw onboard --install-daemon" in out


def test_migrate_idempotent_on_re_run(tmp_path):
    """Running migrate twice yields the same outcome (no state mutation)."""
    home = tmp_path / "voicegw"
    _make_v005_yaml(home)
    _make_v005_db(home)

    first = runner.invoke(app, ["migrate", "--config-home", str(home)])
    second = runner.invoke(app, ["migrate", "--config-home", str(home)])

    assert first.exit_code == 0
    assert second.exit_code == 0
    # Output is byte-identical (idempotent + no time-varying content).
    assert first.output == second.output


# ---------------------------------------------------------------------------
# Cost-tracking disabled (no db)
# ---------------------------------------------------------------------------


def test_migrate_when_only_yaml_present(tmp_path):
    """v0.0.5 install with cost-tracking off has no voicegw.db.
    Migration succeeds; the missing-db note explains the situation.
    """
    home = tmp_path / "voicegw"
    _make_v005_yaml(home)
    # No db on purpose.

    result = runner.invoke(app, ["migrate", "--config-home", str(home)])
    assert result.exit_code == 0, result.output

    out = result.output
    assert "not found (cost-tracking disabled?)" in out


# ---------------------------------------------------------------------------
# Broken yaml -> clear note
# ---------------------------------------------------------------------------


def test_migrate_when_yaml_corrupt(tmp_path):
    """A broken yaml surfaces as a soft warning with a fix pointer."""
    home = tmp_path / "voicegw"
    home.mkdir(parents=True)
    (home / "voicegw.yaml").write_text("not: yaml: at: all:")

    result = runner.invoke(app, ["migrate", "--config-home", str(home)])
    assert result.exit_code == 0, result.output  # informational, not fatal
    assert "did not parse" in result.output
    assert "voicegw init" in result.output


# ---------------------------------------------------------------------------
# Fernet keys: decrypt success and failure
# ---------------------------------------------------------------------------


def test_migrate_reports_managed_providers_count(tmp_path, monkeypatch):
    """Managed providers row count surfaces in output."""
    home = tmp_path / "voicegw"
    _make_v005_yaml(home)
    _make_v005_db(
        home,
        with_managed_providers=[
            ("default:openai", "encrypted-blob-1"),
            ("default:deepgram", "encrypted-blob-2"),
        ],
    )

    # Force decrypt to succeed for any input so we exercise the
    # ok branch deterministically.
    monkeypatch.setattr("voicegateway.core.crypto.decrypt", lambda _: "secret-payload")

    result = runner.invoke(app, ["migrate", "--config-home", str(home)])
    assert result.exit_code == 0, result.output
    assert "2 row(s)" in result.output
    assert "keys decrypt: " in result.output


def test_migrate_warns_when_keys_fail_to_decrypt(tmp_path, monkeypatch):
    """Decrypt failure surfaces as a yellow note + the
    rotate-secret remediation pointer.
    """
    home = tmp_path / "voicegw"
    _make_v005_yaml(home)
    _make_v005_db(home, with_managed_providers=[("default:openai", "garbage")])

    def _raise(_):
        raise ValueError("invalid token")

    monkeypatch.setattr("voicegateway.core.crypto.decrypt", _raise)

    result = runner.invoke(app, ["migrate", "--config-home", str(home)])
    assert result.exit_code == 0, result.output
    assert "did not decrypt" in result.output
    assert "voicegw rotate-secret" in result.output


# ---------------------------------------------------------------------------
# Daemon already registered -> different next-step pointer
# ---------------------------------------------------------------------------


def test_migrate_says_complete_when_daemon_registered(tmp_path, monkeypatch):
    """When the daemon is already registered, the next-step is
    `voicegw status`, not `voicegw onboard`.
    """
    home = tmp_path / "voicegw"
    _make_v005_yaml(home)
    _make_v005_db(home)

    fake = MagicMock()
    fake.status.return_value = {
        "registered": True,
        "running": True,
        "pid": 12345,
    }
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager",
        MagicMock(return_value=fake),
    )

    result = runner.invoke(app, ["migrate", "--config-home", str(home)])
    assert result.exit_code == 0, result.output
    assert "Migration complete" in result.output
    assert "voicegw status" in result.output


def test_migrate_help_renders():
    result = runner.invoke(app, ["migrate", "--help"])
    assert result.exit_code == 0
    assert "Migrate a v0.0.5 install" in result.output


# ---------------------------------------------------------------------------
# Rollback contract (AC-VG-ONBOARD-007): the read-only design IS the
# rollback path. v0.1.0 migrate writes nothing, so there is nothing
# to roll back; the footer surfaces this guarantee for the operator.
# Partial-failure tolerance is verified via the atomic-write helper's
# own contract tests in tests/cli/test_migrate_atomic_write.py.
# ---------------------------------------------------------------------------


def test_migrate_output_states_read_only_guarantee(tmp_path):
    """Every migrate run, regardless of detection outcome, prints
    the read-only footer so the operator knows nothing was written.
    """
    home = tmp_path / "voicegw"
    _make_v005_yaml(home)
    _make_v005_db(home)

    result = runner.invoke(app, ["migrate", "--config-home", str(home)])
    assert result.exit_code == 0, result.output
    # Rich may fold the phrase across lines so assert distinctive
    # substrings rather than the full sentence.
    assert "read-only" in result.output
    assert "no files were written" in result.output
    assert "unchanged" in result.output


def test_migrate_preserves_yaml_byte_for_byte(tmp_path):
    """Run migrate against a v0.0.5 fixture; assert the yaml on disk
    is byte-identical before and after. Pins the read-only contract.
    """
    home = tmp_path / "voicegw"
    cfg = _make_v005_yaml(home)
    _make_v005_db(home)

    pre_bytes = cfg.read_bytes()
    pre_mtime = cfg.stat().st_mtime

    result = runner.invoke(app, ["migrate", "--config-home", str(home)])
    assert result.exit_code == 0, result.output

    # Bytes match.
    assert cfg.read_bytes() == pre_bytes
    # mtime unchanged (no touch).
    assert cfg.stat().st_mtime == pre_mtime


def test_migrate_preserves_db_byte_for_byte(tmp_path):
    """Same contract for the SQLite db: no mtime change, identical bytes."""
    home = tmp_path / "voicegw"
    _make_v005_yaml(home)
    db = _make_v005_db(home, with_managed_providers=[("default:openai", "encrypted")])

    pre_bytes = db.read_bytes()
    pre_mtime = db.stat().st_mtime

    result = runner.invoke(app, ["migrate", "--config-home", str(home)])
    assert result.exit_code == 0, result.output

    assert db.read_bytes() == pre_bytes
    assert db.stat().st_mtime == pre_mtime
