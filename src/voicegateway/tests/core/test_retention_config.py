"""Phase 3 config: retention default surfaces on GatewayConfig."""

from __future__ import annotations

import yaml

from voicegateway.core.config import GatewayConfig


def _write(tmp_path, cfg: dict) -> str:
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump(cfg))
    return str(path)


def test_retention_defaults(tmp_path) -> None:
    cfg = GatewayConfig.load(_write(tmp_path, {"cost_tracking": {"enabled": True}}))
    assert cfg.retention.enabled is True
    assert cfg.retention.default_days == 90


def test_retention_override(tmp_path) -> None:
    cfg = GatewayConfig.load(_write(tmp_path, {"retention": {"default_days": 30}}))
    assert cfg.retention.default_days == 30
