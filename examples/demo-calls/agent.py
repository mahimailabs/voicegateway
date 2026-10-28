"""A demo agent with several call shapes, for filling a VoiceGateway project with varied calls.

One agent name, several scenarios. The caller picks one with the dispatch metadata
(`lk dispatch create --metadata '{"scenario": "slow-llm"}'`); a phone call through a
dispatch rule without metadata gets "phone". Each scenario uses its own LiveKit Inference
models, so the models layer has more than one provider to compare.

Both connections are on: LiveKit's uploader (LIVEKIT_OBSERVABILITY_URL, with record=True)
sends spans, turns and the recording, and voicegateway.attach() puts the scenario on the
call as a custom field, so the call list can filter by it.
"""

import json
import logging
from dataclasses import dataclass, field

import voicegateway
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    RunContext,
    function_tool,
    room_io,
)
from livekit.agents.llm import Tool

load_dotenv()

logger = logging.getLogger("demo-calls")

AGENT_NAME = "voicegateway-demo"

BAKERY = (
    "You answer the phone for Corner Bakery, a small bakery. Speak in plain sentences with no "
    "lists or markdown. The bakery opens at 7 am and closes at 6 pm, Monday to Saturday."
)

# The bakery's orders, for the tool call. A real agent would call its order system here.
ORDERS = {
    "1234": "ready for pickup since 9 am",
    "5678": "in the oven, ready at 3 pm",
}


@function_tool
async def check_order(context: RunContext[None], order_number: str) -> str:
    """Look up the status of a customer's order.

    Args:
        order_number: The order number the caller gives, digits only.
    """
    status = ORDERS.get(order_number.strip())
    if status is None:
        return f"There is no order {order_number}."
    return f"Order {order_number} is {status}."


@dataclass(frozen=True)
class Scenario:
    stt: str
    llm: str
    tts: str
    instructions: str
    tools: list[Tool] = field(default_factory=list)


SCENARIOS = {
    # A short question on a fast stack: the baseline the others are compared with.
    "quick": Scenario(
        stt="deepgram/nova-3:en",
        llm="openai/gpt-4.1-mini",
        tts="cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
        instructions=BAKERY + " Answer in one short sentence.",
    ),
    # A reasoning model on a question that needs some working out: a slower model turn.
    "slow-llm": Scenario(
        stt="assemblyai/universal-3-6-pro:en",
        llm="openai/gpt-5",
        tts="deepgram/aura-2:athena",
        instructions=BAKERY
        + " Cakes cost 4 dollars a serving; nut-free and dairy-free cakes cost 1 dollar more a "
        "serving. Work out prices exactly, then answer in two sentences.",
    ),
    # A long answer the caller cuts into: an interruption in the middle of agent speech.
    "interruption": Scenario(
        stt="deepgram/nova-3:en",
        llm="openai/gpt-4.1-mini",
        tts="fishaudio/s2.1-pro:9a9cf47702da476aa4629e2506d4a857",
        instructions=BAKERY
        + " When asked about the cakes, describe all six of them in detail: chocolate, carrot, "
        "lemon, red velvet, cheesecake and black forest. The chocolate cake is 32 dollars.",
    ),
    # An order lookup through a function tool.
    "tool-call": Scenario(
        stt="deepgram/flux-general-en",
        llm="google/gemma-4-31b-it",
        tts="rime/coda:astra",
        instructions=BAKERY + " Use check_order for any order question. Answer in one sentence.",
        tools=[check_order],
    ),
}
# A phone call through the SIP dispatch rule: the order lookup, on a phone line.
SCENARIOS["phone"] = SCENARIOS["tool-call"]


def scenario_name(metadata: str) -> str:
    """Read the scenario from the dispatch metadata; anything else is a phone call."""
    try:
        name = json.loads(metadata).get("scenario") if metadata else None
    except (ValueError, AttributeError):
        name = None
    return name if isinstance(name, str) and name in SCENARIOS else "phone"


# start mode refuses jobs above 70 % CPU load by default; a laptop running other work beside
# the agent is often above that. The demo runs one call at a time.
server = AgentServer(load_threshold=0.95)


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: agents.JobContext) -> None:
    name = scenario_name(ctx.job.metadata)
    scenario = SCENARIOS[name]
    logger.info("scenario %s", name)
    session: AgentSession[None] = AgentSession(
        stt=scenario.stt,
        llm=scenario.llm,
        tts=scenario.tts,
    )
    voicegateway.attach(session, fields={"scenario": name, "demo": "corner-bakery"})
    await session.start(
        room=ctx.room,
        agent=Agent(instructions=scenario.instructions, tools=list(scenario.tools)),
        # Recording is what makes LiveKit Agents upload the session.
        record=True,
        room_options=room_io.RoomOptions(delete_room_on_close=True),
    )
    if name == "phone":
        await session.generate_reply(instructions="Greet the caller and ask how you can help.")


if __name__ == "__main__":
    agents.cli.run_app(server)
