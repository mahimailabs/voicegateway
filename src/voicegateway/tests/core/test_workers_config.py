"""Phase 3 config: worker cadence surfaces on GatewayConfig."""

from __future__ import annotations

import yaml

from voicegateway.core.config import GatewayConfig


def _write(tmp_path, cfg: dict) -> str:
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump(cfg))
    return str(path)


def test_workers_defaults(tmp_path) -> None:
    cfg = GatewayConfig.load(_write(tmp_path, {"cost_tracking": {"enabled": True}}))
    assert cfg.workers.enabled is True
    assert cfg.workers.rollup_interval_seconds == 900
    assert cfg.workers.retention_interval_seconds == 3600


def test_workers_override(tmp_path) -> None:
    cfg = GatewayConfig.load(
        _write(
            tmp_path,
            {"workers": {"enabled": False, "rollup_interval_seconds": 111}},
        )
    )
    assert cfg.workers.enabled is False
    assert cfg.workers.rollup_interval_seconds == 111
