"""v0.0.5 wedge: drop-in for ``livekit.agents.inference``.

This example shows the headline migration: only the import line changes
between LiveKit Cloud Inference and self-hosted VoiceGateway.

Configure your providers in ``voicegw.yaml`` (or rely on the per-project
keys exposed via the dashboard / MCP), then run:

    python examples/v005_inference_drop_in.py dev

Requires:
    - OPENAI_API_KEY (or equivalent) set in your environment, OR
      a configured project in voicegw.yaml carrying an openai entry.
    - The standard livekit-agents + livekit-plugins-openai installs:
      pip install "voicegateway[openai,deepgram,cartesia]"
"""

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import silero

# The migration is one line. Before:
#   from livekit.agents import inference
# After:
from voicegateway import inference


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    # The factories return wrapped LiveKit-plugin instances. AgentSession
    # consumes them identically to plain LK plugins; cost tracking and
    # session correlation happen transparently in the middleware.
    session = AgentSession(
        vad=silero.VAD.load(),
        stt=inference.STT("deepgram/nova-3:en"),
        llm=inference.LLM("openai/gpt-4o-mini"),
        tts=inference.TTS("cartesia/sonic-3"),
    )

    await session.start(
        agent=Agent(instructions="You are a helpful voice assistant."),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
