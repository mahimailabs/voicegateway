"""Agent with resolver-time fallback configured in voicegw.yaml.

Resolver-time fallback walks a chain at agent startup and constructs
the first model whose provider is reachable. Once ``AgentSession``
starts, that resolved model is used for the whole call: VoiceGateway
does not swap providers mid-call. For runtime mid-call failover,
compose ``LiveKit's FallbackAdapter`` around VG ``inference.*``
instances directly.

Configure the chain in voicegw.yaml::

    fallbacks:
      stt: [deepgram/nova-3, groq/whisper-large-v3, local/whisper-large-v3]
      llm: [openai/gpt-4.1-mini, groq/llama-3.3-70b-versatile, ollama/qwen2.5:3b]
      tts: [cartesia/sonic-3, elevenlabs/eleven_turbo_v2_5, local/kokoro]

Then construct the inference factories with the primary model id; if
its provider is not configured or its plugin is missing, walk the
chain manually:

Usage:
    python examples/fallback_agent.py dev
"""

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import silero

from voicegateway import inference


def _first_resolvable(modality: str, chain: list[str]):
    """Return the first inference instance the chain can build.

    Walks the chain at startup and returns the first model whose
    provider plugin imports cleanly and whose key resolves. Re-raises
    the last error when every model fails.
    """
    factory = {
        "stt": inference.STT,
        "llm": inference.LLM,
        "tts": inference.TTS,
    }[modality]
    last_error: Exception | None = None
    for model_id in chain:
        try:
            return factory(model_id)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    assert last_error is not None
    raise last_error


# Mirror the voicegw.yaml fallbacks: blocks. v0.0.6 adds an explicit
# ``fallback=`` parameter to the inference factories so this manual
# walk goes away.
STT_CHAIN = ["deepgram/nova-3", "groq/whisper-large-v3", "local/whisper-large-v3"]
LLM_CHAIN = [
    "openai/gpt-4.1-mini",
    "groq/llama-3.3-70b-versatile",
    "ollama/qwen2.5:3b",
]
TTS_CHAIN = ["cartesia/sonic-3", "elevenlabs/eleven_turbo_v2_5", "local/kokoro"]


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=_first_resolvable("stt", STT_CHAIN),
        llm=_first_resolvable("llm", LLM_CHAIN),
        tts=_first_resolvable("tts", TTS_CHAIN),
    )

    await session.start(
        agent=Agent(
            instructions=(
                "You are a helpful voice assistant with high availability. "
                "If the primary AI service is unreachable at startup, the "
                "next backup in the chain takes over."
            )
        ),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
