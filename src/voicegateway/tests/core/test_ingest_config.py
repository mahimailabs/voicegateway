"""Phase 3 config: ingest rate-limit knobs surface on GatewayConfig."""

from __future__ import annotations

import yaml

from voicegateway.core.config import GatewayConfig


def _write(tmp_path, cfg: dict) -> str:
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump(cfg))
    return str(path)


def test_ingest_defaults(tmp_path) -> None:
    cfg = GatewayConfig.load(_write(tmp_path, {"cost_tracking": {"enabled": True}}))
    assert cfg.ingest.enabled is True
    assert cfg.ingest.requests_per_minute == 120
    assert cfg.ingest.burst == 240
    assert cfg.ingest.max_batch_size == 1000


def test_ingest_overrides(tmp_path) -> None:
    cfg = GatewayConfig.load(
        _write(
            tmp_path,
            {"ingest": {"requests_per_minute": 30, "burst": 60, "max_batch_size": 100}},
        )
    )
    assert cfg.ingest.requests_per_minute == 30
    assert cfg.ingest.burst == 60
    assert cfg.ingest.max_batch_size == 100
