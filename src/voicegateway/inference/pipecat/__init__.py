"""Pipecat-scoped VoiceGateway internals (import ``pipecat`` on load).

Modules under this package subclass ``pipecat`` types (``BaseObserver``) and
therefore import ``pipecat`` when imported. They are loaded lazily by the
framework-neutral ``voicegateway.attach()`` dispatcher and the lazily-resolved
``voicegateway.Observer`` export, so importing ``voicegateway`` itself stays
free of any framework import.
"""
