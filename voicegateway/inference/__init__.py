"""Drop-in mirror of `livekit.agents.inference` backed by VoiceGateway.

Users replace::

    from livekit.agents import inference

with::

    from voicegateway import inference

and keep the rest of their agent code identical. The VG-side STT/LLM/TTS
factories construct the underlying ``livekit.plugins.<provider>`` instance
with the project's configured key, then wrap it in an
``Instrumented{STT,LLM,TTS}`` so cost, latency, and session correlation
are recorded transparently.

Public re-exports are wired in the next iteration; this module is a
package skeleton for v0.0.5.
"""
