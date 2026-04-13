"""Basic voice agent using cloud providers via the gateway.

Uses Deepgram for STT, OpenAI for LLM, and Cartesia for TTS.
Requires API keys configured in gateway.yaml or environment variables.

Usage:
    python examples/basic_agent.py dev
"""

from livekit.agents import AgentSession, Agent, JobContext, WorkerOptions, cli
from livekit.plugins import silero
from voicegateway import Gateway

gw = Gateway()


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=gw.stt("deepgram/nova-3"),
        llm=gw.llm("openai/gpt-4.1-mini"),
        tts=gw.tts("cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"),
    )

    await session.start(
        agent=Agent(instructions="You are a helpful voice assistant."),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
