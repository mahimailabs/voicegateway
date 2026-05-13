"""VoiceGateway: cost tracking and reconciliation for LiveKit voice agents.

The single public Python surface is :mod:`voicegateway.inference`, a
drop-in mirror of ``livekit.agents.inference``::

    from voicegateway import inference

    stt = inference.STT("deepgram/nova-3")
    llm = inference.LLM("openai/gpt-4o-mini")
    tts = inference.TTS("cartesia/sonic-3")

Operations live outside the SDK: ``voicegw <subcommand>`` for the CLI,
``GET /v1/...`` for the HTTP API, the dashboard at
``http://localhost:9090``, or the MCP tools.
"""

from voicegateway import inference
from voicegateway._version import __version__

__all__ = ["inference", "__version__"]
