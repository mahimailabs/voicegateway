"""Parity tests: ApplicationBuilder.build() vs build_app() shim."""

from __future__ import annotations

import pytest

from voicegateway.core.gateway import Gateway
from voicegateway.server.main import ApplicationBuilder, build_app


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "appbuilder.db"))
    return Gateway(config_path=temp_config)


def test_builder_and_shim_produce_equivalent_apps(gateway) -> None:
    app_via_class = ApplicationBuilder(gateway).build()
    app_via_shim = build_app(gateway)

    assert len(app_via_class.routes) == len(app_via_shim.routes)
    assert app_via_class.state.gateway is gateway
    assert app_via_shim.state.gateway is gateway
    assert len(app_via_class.exception_handlers) == len(app_via_shim.exception_handlers)


def test_optional_features_can_be_disabled(gateway) -> None:
    full = ApplicationBuilder(gateway).build()
    minimal = ApplicationBuilder(
        gateway, enable_mcp_sse=False, enable_dashboard=False
    ).build()

    assert len(minimal.routes) <= len(full.routes)
    assert minimal.state.gateway is gateway


def test_app_state_carries_gateway_and_keys(gateway) -> None:
    app = ApplicationBuilder(gateway).build()

    assert app.state.gateway is gateway
    assert isinstance(app.state.api_keys, list)
    assert isinstance(app.state.started_at, float)
