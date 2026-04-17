"""VoiceGateway — self-hosted inference gateway for voice AI."""

from voicegateway.core.config import GatewayConfig
from voicegateway.core.gateway import Gateway
from voicegateway.core.model_id import ModelId

__all__ = ["Gateway", "ModelId", "GatewayConfig"]
__version__ = "0.1.0"
