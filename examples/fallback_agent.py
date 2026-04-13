"""Agent with automatic fallback chains.

If the primary model fails (API error, timeout, rate limit), the gateway
automatically tries the next model in the chain — transparently.

Fallback chains are configured in gateway.yaml:
    fallbacks:
      stt: [deepgram/nova-3, groq/whisper-large-v3, local/whisper-large-v3]
      llm: [openai/gpt-4.1-mini, groq/llama-3.1-70b, ollama/qwen2.5:3b]
      tts: [cartesia/sonic-3, elevenlabs/eleven_turbo_v2_5, local/kokoro]

Usage:
    python examples/fallback_agent.py dev
"""

from livekit.agents import AgentSession, Agent, JobContext, WorkerOptions, cli
from livekit.plugins import silero
from voicegateway import Gateway

gw = Gateway()


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    # Use fallback chains — if primary fails, automatically tries alternatives
    session = AgentSession(
        vad=silero.VAD.load(),
        stt=gw.stt_with_fallback(),
        llm=gw.llm_with_fallback(),
        tts=gw.tts_with_fallback(),
    )

    await session.start(
        agent=Agent(
            instructions="You are a helpful voice assistant with high availability. "
            "If any AI service goes down, backups kick in automatically."
        ),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
