"""The example agent with voicegateway.attach() instead of LiveKit's uploader.

It needs no LIVEKIT_OBSERVABILITY_URL. The SDK sends the session's state, messages
with their metrics, usage, tool calls and the custom field below to VOICEGATEWAY_URL, signed with
LIVEKIT_API_KEY and LIVEKIT_API_SECRET. Run it with `make example-call ATTACH=1`.
"""

import voicegateway
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, room_io

load_dotenv()

AGENT_NAME = "voicegateway-example"

server = AgentServer(load_threshold=0.95)


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: agents.JobContext) -> None:
    session: AgentSession[None] = AgentSession(
        stt="assemblyai/universal-3-6-pro:en",
        llm="google/gemma-4-31b-it",
        tts="fishaudio/s2.1-pro:fa4c9eb3dccc4806b382b40d61c6b10a",
    )
    # Before start, so the first states are captured. Flushes on close and job shutdown.
    voicegateway.attach(session, fields={"example": "attach"})
    await session.start(
        room=ctx.room,
        agent=Agent(instructions="You answer for a small bakery in one short sentence."),
        room_options=room_io.RoomOptions(delete_room_on_close=True),
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
