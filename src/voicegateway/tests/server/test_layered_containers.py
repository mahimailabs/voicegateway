"""The DI containers are layered, rooted at the Gateway, and fail fast."""

from __future__ import annotations

import pytest
from dependency_injector import containers, errors, providers

from voicegateway.core.container import (
    Container,
    InfraContainer,
    ServicesContainer,
)
from voicegateway.core.gateway import Gateway


@pytest.fixture
def gateway(monkeypatch, tmp_path) -> Gateway:
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "layer.db"))
    return Gateway(require_config=False)


def _container(gw: Gateway) -> Container:
    container = Container()
    container.core.gateway.override(providers.Object(gw))
    container.check_dependencies()
    return container


def test_config_and_storage_resolve_through_the_gateway(gateway) -> None:
    container = _container(gateway)

    assert container.core.config() is gateway.config
    assert container.core.storage() is gateway.storage


def test_a_container_without_a_gateway_fails_the_startup_check() -> None:
    with pytest.raises(errors.Error, match="gateway"):
        Container().check_dependencies()


def test_a_layer_without_its_input_fails_the_startup_check() -> None:
    class Broken(containers.DeclarativeContainer):
        infra = providers.Container(InfraContainer)  # no config or storage passed
        services = providers.Container(ServicesContainer, infra=infra)

    with pytest.raises(errors.Error, match="config"):
        Broken().check_dependencies()
