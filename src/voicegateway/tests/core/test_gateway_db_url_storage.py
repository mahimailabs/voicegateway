"""VOICEGW_DB_URL (the Postgres collector backend) enables storage.

A Docker fleet collector points at its database with VOICEGW_DB_URL alone; that
must turn storage on, otherwise POST /v1/ingest returns 503 and the collector
persists nothing. A sqlite URL stands in for Postgres so the test needs no PG.
"""

from __future__ import annotations

import yaml

from voicegateway.core.gateway import Gateway


def _cfg(tmp_path) -> str:
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump({"cost_tracking": {"enabled": False}}))
    return str(path)


def test_db_url_enables_storage(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    monkeypatch.setenv("VOICEGW_DB_URL", f"sqlite+aiosqlite:///{tmp_path}/collector.db")
    gw = Gateway(config_path=_cfg(tmp_path))
    assert gw.storage is not None


def test_no_db_url_no_path_keeps_storage_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    monkeypatch.delenv("VOICEGW_DB_URL", raising=False)
    gw = Gateway(config_path=_cfg(tmp_path))
    assert gw.storage is None
