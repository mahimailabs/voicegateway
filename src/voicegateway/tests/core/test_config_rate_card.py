"""The ``rate_card:`` config block: parsed into GatewayConfig and wired into
the gateway's CostTracker so YAML-seeded rating is live server-side.
"""

from __future__ import annotations

import pytest
import yaml

from voicegateway.billing.rate_card import RateCard
from voicegateway.core.config import ConfigError, GatewayConfig
from voicegateway.core.gateway import Gateway


def _write(tmp_path, cfg: dict) -> str:
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump(cfg))
    return str(path)


def test_missing_rate_card_defaults_to_empty(tmp_path) -> None:
    cfg_path = _write(tmp_path, {"cost_tracking": {"enabled": False}})
    config = GatewayConfig.load(cfg_path)
    assert config.rate_card == {}
    card = RateCard.from_config(config.rate_card)
    assert card.rules == []
    assert card.default_markup == 1.0


def test_rate_card_block_parses_into_config(tmp_path) -> None:
    cfg_path = _write(
        tmp_path,
        {
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
        },
    )
    config = GatewayConfig.load(cfg_path)
    assert config.rate_card["default_markup"] == 1.3
    card = RateCard.from_config(config.rate_card)
    assert card.default_markup == 1.3
    assert len(card.rules) == 2


def test_rate_card_rule_with_unknown_key_is_rejected(tmp_path) -> None:
    cfg_path = _write(
        tmp_path,
        {
            "cost_tracking": {"enabled": False},
            "rate_card": {"rules": [{"provider": "deepgram", "markkup": 1.5}]},
        },
    )
    with pytest.raises(ConfigError):
        GatewayConfig.load(cfg_path)


def test_fixed_rule_without_valid_unit_is_rejected(tmp_path) -> None:
    # A fixed rule with a missing/invalid unit must fail at config load as a
    # ConfigError, not crash later inside RateCard.from_config.
    cfg_path = _write(
        tmp_path,
        {
            "cost_tracking": {"enabled": False},
            "rate_card": {"rules": [{"provider": "deepgram", "fixed": 0.006}]},
        },
    )
    with pytest.raises(ConfigError):
        GatewayConfig.load(cfg_path)

    bad_unit = _write(
        tmp_path,
        {
            "cost_tracking": {"enabled": False},
            "rate_card": {
                "rules": [{"provider": "deepgram", "fixed": 0.006, "unit": "furlong"}]
            },
        },
    )
    with pytest.raises(ConfigError):
        GatewayConfig.load(bad_unit)


def test_rule_with_both_markup_and_fixed_is_rejected(tmp_path) -> None:
    cfg_path = _write(
        tmp_path,
        {
            "cost_tracking": {"enabled": False},
            "rate_card": {
                "rules": [
                    {
                        "provider": "deepgram",
                        "markup": 1.5,
                        "fixed": 0.006,
                        "unit": "minute",
                    }
                ]
            },
        },
    )
    with pytest.raises(ConfigError):
        GatewayConfig.load(cfg_path)


def test_gateway_wires_rate_card_into_cost_tracker(tmp_path) -> None:
    """A gateway built with a rate_card rates each record with that card."""
    cfg_path = _write(
        tmp_path,
        {
            "providers": {"deepgram": {"api_key": "k"}},
            "cost_tracking": {"enabled": False},
            "rate_card": {"rules": [{"provider": "deepgram", "markup": 1.5}]},
        },
    )
    gw = Gateway(config_path=cfg_path)
    record = gw.cost_tracker.create_record(
        model_id="deepgram/nova-3",
        modality="stt",
        provider="deepgram",
        input_units=1.0,
    )
    assert record.cost_usd == pytest.approx(0.0048)
    assert record.rated_price_usd == pytest.approx(0.0072)
    assert record.rate_rule == "cost_plus:1.5"


async def test_gateway_merges_db_override_over_seed(tmp_path, monkeypatch) -> None:
    """A DB rate rule overrides the YAML seed at the same scope after refresh."""
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "gw-override.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    cfg_path = _write(
        tmp_path,
        {
            "providers": {"deepgram": {"api_key": "k"}},
            "cost_tracking": {"enabled": True},
            "rate_card": {"rules": [{"provider": "deepgram", "markup": 1.5}]},
        },
    )
    gw = Gateway(config_path=cfg_path)
    # DB override for the same scope with a higher markup.
    await gw.storage.upsert_rate_rule(provider="deepgram", markup=1.9)
    await gw.refresh_config()

    record = gw.cost_tracker.create_record(
        model_id="deepgram/nova-3",
        modality="stt",
        provider="deepgram",
        input_units=1.0,
    )
    assert record.rate_rule == "cost_plus:1.9"  # DB override wins over seed 1.5
    assert record.rated_price_usd == pytest.approx(0.0048 * 1.9)
