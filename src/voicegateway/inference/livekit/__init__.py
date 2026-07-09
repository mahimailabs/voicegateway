"""LiveKit-scoped VoiceGateway internals (import ``livekit`` on load).

Modules under this package subclass ``livekit.agents`` types and therefore
import ``livekit`` when imported. They are loaded lazily by the framework-neutral
``voicegateway.guard()`` dispatcher, so importing ``voicegateway`` itself stays
free of any framework import.
"""
