"""Smoke tests for the ``voicegw prices`` command group.

ls + sync are config-only (no DB), so they exercise command wiring, config
loading, RateCard construction, and the catalog base-cost lookup. The
aggregation behind ``reconcile`` is covered by the billing repository tests
and the pure margin_reconcile tests.
"""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

from voicegateway.cli import app

runner = CliRunner()

_CFG = {
    "providers": {"deepgram": {"api_key": "k"}},
    "models": {"stt": {}, "llm": {}, "tts": {}},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": False},
    "rate_card": {
        "default_markup": 1.3,
        "rules": [
            {"provider": "deepgram", "markup": 1.5},
            {
                "modality": "stt",
                "provider": "deepgram",
                "model": "nova-3",
                "fixed": 0.0060,
                "unit": "minute",
            },
        ],
    },
}


def _cfg(tmp_path) -> str:
    p = tmp_path / "voicegw.yaml"
    p.write_text(yaml.dump(_CFG))
    return str(p)


def test_prices_group_is_registered() -> None:
    result = runner.invoke(app, ["prices", "--help"])
    assert result.exit_code == 0
    assert "ls" in result.output
    assert "reconcile" in result.output
    assert "sync" in result.output


def test_prices_ls_prints_card(tmp_path) -> None:
    result = runner.invoke(app, ["prices", "ls", "--config", _cfg(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "default markup" in result.output


def test_prices_sync_runs_against_catalog(tmp_path) -> None:
    result = runner.invoke(app, ["prices", "sync", "--config", _cfg(tmp_path)])
    assert result.exit_code == 0, result.output


def test_prices_ls_empty_card(tmp_path) -> None:
    cfg = {"cost_tracking": {"enabled": False}}
    p = tmp_path / "voicegw.yaml"
    p.write_text(yaml.dump(cfg))
    result = runner.invoke(app, ["prices", "ls", "--config", str(p)])
    assert result.exit_code == 0, result.output
    assert "default markup" in result.output


def test_prices_set_ls_rm_roundtrip(tmp_path, monkeypatch) -> None:
    # chdir so alembic env.py + the CLI resolve the same tmp config (no stray).
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    cfg = {
        "cost_tracking": {"enabled": True, "db_path": "prices.db"},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
    }
    (tmp_path / "voicegw.yaml").write_text(yaml.dump(cfg))

    # set a DB override
    r = runner.invoke(app, ["prices", "set", "--provider", "openai", "--markup", "1.5"])
    assert r.exit_code == 0, r.output
    assert "openai" in r.output  # the printed rule_id (*|*|*|openai|*)

    # ls now runs against the effective card (exercises the DB read path)
    r = runner.invoke(app, ["prices", "ls"])
    assert r.exit_code == 0, r.output

    # rm removes it
    r = runner.invoke(app, ["prices", "rm", "--provider", "openai"])
    assert r.exit_code == 0, r.output

    # rm again: nothing to remove
    r = runner.invoke(app, ["prices", "rm", "--provider", "openai"])
    assert r.exit_code != 0


def test_prices_set_rejects_markup_and_fixed(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    cfg = {
        "cost_tracking": {"enabled": True, "db_path": "prices2.db"},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
    }
    (tmp_path / "voicegw.yaml").write_text(yaml.dump(cfg))
    r = runner.invoke(
        app,
        [
            "prices",
            "set",
            "--provider",
            "openai",
            "--markup",
            "1.5",
            "--fixed",
            "0.006",
            "--unit",
            "minute",
        ],
    )
    assert r.exit_code == 2
