"""Application container: a thin facade over :class:`Gateway`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicegateway.core.config import GatewayConfig
    from voicegateway.core.gateway import Gateway
    from voicegateway.middleware.cost_tracker import CostTracker
    from voicegateway.storage.sqlite import SQLiteStorage


@dataclass(frozen=True)
class Container:
    """Holds the shared :class:`Gateway` and exposes its collaborators."""

    gateway: Gateway

    @classmethod
    def from_config_path(cls, config_path: str | None = None) -> Container:
        """Construct a Gateway from disk and wrap it."""
        from voicegateway.core.gateway import Gateway

        return cls(gateway=Gateway(config_path=config_path))

    @property
    def config(self) -> GatewayConfig:
        return self.gateway.config

    @property
    def storage(self) -> SQLiteStorage | None:
        return self.gateway.storage

    @property
    def cost_tracker(self) -> CostTracker:
        return self.gateway.cost_tracker
