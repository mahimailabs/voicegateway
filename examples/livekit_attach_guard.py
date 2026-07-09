"""Minimal LiveKit agent: native plugins + voicegateway.attach() + guard().

Shows the framework-neutral shape on LiveKit:

- Build an ``AgentSession`` from native ``livekit.plugins`` providers.
- Wrap ONE provider (the LLM) with ``voicegateway.guard(...)`` for fallback,
  rate limiting, and a daily spend cap. ``guard`` returns a drop-in of the same
  LiveKit type, so it slots into the session unchanged.
- Call ``voicegateway.attach(session)`` once: the single passive meter for cost
  and latency. ``guard`` writes no metrics, so ``attach`` + ``guard`` never
  double-count.

Run shape (this file is import-clean without the plugin wheels or keys; the
native ``livekit.plugins`` imports are deferred into ``build_session`` so
importing the module does not require them)::

    pip install "voicegateway[livekit,openai,deepgram,cartesia]"
    export OPENAI_API_KEY=... DEEPGRAM_API_KEY=... CARTESIA_API_KEY=...
    export LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
    python examples/livekit_attach_guard.py start

The last line is the standard ``livekit-agents`` CLI entry; it needs a running
LiveKit server and the credentials above.
"""

from __future__ import annotations

from typing import Any

import voicegateway

PROJECT = "livekit-demo"


def build_session() -> Any:
    """Construct an ``AgentSession`` with native plugins and one guarded LLM.

    The native provider imports live here (not at module scope) so importing
    this example never requires the ``livekit-plugins-*`` wheels; only running
    ``build_session()`` does.
    """
    from livekit.agents import AgentSession
    from livekit.plugins import cartesia, deepgram, openai

    return AgentSession(
        # STT and TTS run as plain native providers: attach() meters them.
        stt=deepgram.STT(model="nova-3"),
        # guard() wraps the LLM for control: on a primary error it falls back to
        # gpt-4o, throttles at 60 requests/min, and blocks past $5.00/day.
        llm=voicegateway.guard(
            openai.LLM(model="gpt-4o-mini"),
            fallback=[openai.LLM(model="gpt-4o")],
            rate_limit="60/min",
            budget="$5.00/day",
            project=PROJECT,
        ),
        tts=cartesia.TTS(model="sonic-3"),
    )


async def entrypoint(ctx: Any) -> None:
    """LiveKit worker entrypoint: attach the meter, then start the session."""
    from livekit.agents import Agent

    await ctx.connect()

    session = build_session()

    # One call. Every STT / LLM / TTS metric from this session is priced and
    # recorded from here on. attach() returns the correlation session id.
    session_id = voicegateway.attach(session, project=PROJECT)
    print(f"voicegateway attached: session_id={session_id}")

    await session.start(
        agent=Agent(instructions="You are a helpful voice assistant. Be concise."),
        room=ctx.room,
    )


def main() -> None:
    """Run the agent through the standard ``livekit-agents`` worker CLI."""
    from livekit.agents import WorkerOptions, cli

    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    main()
