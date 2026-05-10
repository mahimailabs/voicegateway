"""100% offline voice agent: zero API keys, zero internet.

Uses local Whisper for STT, Ollama for LLM, and Kokoro for TTS. All
models run on this machine.

Prerequisites:
    - Ollama running locally with qwen2.5:3b pulled
    - pip install voicegateway[local]

Usage:
    python examples/local_only_agent.py dev
"""

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import silero

from voicegateway import inference


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=inference.STT("local/whisper-large-v3"),
        llm=inference.LLM("ollama/qwen2.5:3b"),
        tts=inference.TTS("local/kokoro:af_heart"),
    )

    await session.start(
        agent=Agent(
            instructions=(
                "You are a helpful voice assistant running entirely "
                "locally. All processing happens on this machine, no "
                "data leaves the device."
            )
        ),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
