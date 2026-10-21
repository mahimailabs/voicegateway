"""The smallest LiveKit Agents voice agent that VoiceGateway can profile.

LiveKit Inference runs speech to text, the model and text to speech, so the
agent needs only LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET. With
LIVEKIT_OBSERVABILITY_URL set, LiveKit Agents uploads the session's traces,
logs, transcript and recording there; it uploads only when the session records.
"""

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, room_io

load_dotenv()

# The caller dispatches the agent by this name (lk dispatch create --agent-name).
AGENT_NAME = "voicegateway-example"

server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: agents.JobContext) -> None:
    session: AgentSession[None] = AgentSession(
        stt="assemblyai/universal-3-6-pro:en",
        llm="google/gemma-4-31b-it",
        tts="fishaudio/s2.1-pro:fa4c9eb3dccc4806b382b40d61c6b10a",
    )
    await session.start(
        room=ctx.room,
        agent=Agent(instructions="You answer for a small bakery in one short sentence."),
        # Recording is what makes LiveKit Agents upload the session.
        record=True,
        # Ending the room when the caller leaves sends room_finished at once,
        # so the call closes without waiting for LiveKit's empty-room timeout.
        room_options=room_io.RoomOptions(delete_room_on_close=True),
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
