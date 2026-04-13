"""Hybrid agent — best of cloud and local.

Uses cloud Deepgram STT (best accuracy), local Ollama LLM (free, private),
and cloud Cartesia TTS (lowest latency).

Usage:
    python examples/hybrid_agent.py dev
"""

from livekit.agents import AgentSession, Agent, JobContext, WorkerOptions, cli
from livekit.plugins import silero
from voicegateway import Gateway

gw = Gateway()


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=gw.stt("deepgram/nova-3"),          # Cloud: best accuracy
        llm=gw.llm("ollama/qwen2.5:7b"),         # Local: free, private
        tts=gw.tts("cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"),  # Cloud: low latency
    )

    await session.start(
        agent=Agent(
            instructions="You are a helpful voice assistant. "
            "Your speech recognition and voice are cloud-powered for quality, "
            "but your thinking happens locally for privacy."
        ),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
