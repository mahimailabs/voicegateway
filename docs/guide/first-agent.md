---
title: First agent
description: A complete worked agent for LiveKit and Pipecat with attach() for cost metering and a guard() fallback example. Copy, set your keys, run.
---

# First agent

This page gives you a complete agent file for each framework. Each example uses
`attach()` to meter cost and latency, and `guard()` to add a fallback LLM. Copy
the file for your framework, set your environment variables, and run it.

## Prerequisites

- Python 3.11 or later
- VoiceGateway installed for your framework (see [Installation](/guide/installation))
- API keys for Deepgram, OpenAI, and Cartesia (or swap for your own providers)

<Tabs>
  <Tab title="LiveKit">
    ### Install

    <CodeGroup>
    ```bash uv
    uv pip install "voicegateway[openai,deepgram,cartesia]"
    pip install "livekit-agents[silero]"
    ```
    ```bash pip
    pip install "voicegateway[openai,deepgram,cartesia]"
    pip install "livekit-agents[silero]"
    ```
    </CodeGroup>

    ### Set environment variables

    ```bash
    export LIVEKIT_URL=wss://your-project.livekit.cloud
    export LIVEKIT_API_KEY=your-livekit-key
    export LIVEKIT_API_SECRET=your-livekit-secret

    export DEEPGRAM_API_KEY=your-deepgram-key
    export OPENAI_API_KEY=your-openai-key
    export CARTESIA_API_KEY=your-cartesia-key
    ```

    For local development with `livekit-server --dev`, use:

    ```bash
    export LIVEKIT_URL=ws://localhost:7880
    export LIVEKIT_API_KEY=devkey
    export LIVEKIT_API_SECRET=secret
    ```

    ### agent.py

    ```python
    """LiveKit voice agent with VoiceGateway cost metering and LLM fallback."""

    import os

    from livekit.agents import Agent, AgentSession, WorkerOptions, cli
    from livekit.plugins import cartesia, deepgram, openai

    import voicegateway


    class MyAgent(Agent):
        def __init__(self) -> None:
            super().__init__(
                instructions=(
                    "You are a friendly voice assistant. Keep your answers short "
                    "and clear. One or two sentences unless asked for more."
                ),
            )


    async def entrypoint(ctx) -> None:
        await ctx.connect()

        # guard() wraps the LLM with a fallback and a daily budget.
        # It returns a drop-in openai.LLM, so AgentSession sees no difference.
        guarded_llm = voicegateway.guard(
            openai.LLM(model="gpt-4o-mini"),
            fallback=[openai.LLM(model="gpt-4o")],
            rate_limit="60/min",
            budget="$5.00/day",
            project="my-agent",
        )

        session = AgentSession(
            stt=deepgram.STT(model="nova-3"),
            llm=guarded_llm,
            tts=cartesia.TTS(model="sonic-3"),
        )

        # attach() is the single meter. Call it once, before session.start().
        voicegateway.attach(session, project="my-agent")

        await session.start(agent=MyAgent(), room=ctx.room)


    if __name__ == "__main__":
        cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
    ```

    ### Run the agent

    ```bash
    python agent.py dev
    ```

    Connect to your LiveKit room from a browser or the LiveKit Playground
    (`https://playground.livekit.io`), say something, and watch the dashboard.

    ### What happens

    1. `deepgram.STT` transcribes speech. `attach()` meters audio minutes and cost.
    2. `guard(openai.LLM(...))` sends the transcript to GPT-4o mini. If GPT-4o mini
       returns an error, `guard()` retries with GPT-4o automatically. `attach()`
       meters prompt tokens, completion tokens, and cost.
    3. `cartesia.TTS` synthesizes speech. `attach()` meters characters and cost.
    4. Every row lands in the dashboard at `http://127.0.0.1:9090`.
  </Tab>

  <Tab title="Pipecat">
    ### Install

    <CodeGroup>
    ```bash uv
    uv pip install "voicegateway[pipecat]"
    uv pip install "pipecat-ai[openai,deepgram,cartesia,daily]"
    ```
    ```bash pip
    pip install "voicegateway[pipecat]"
    pip install "pipecat-ai[openai,deepgram,cartesia,daily]"
    ```
    </CodeGroup>

    ### Set environment variables

    ```bash
    export DEEPGRAM_API_KEY=your-deepgram-key
    export OPENAI_API_KEY=your-openai-key
    export CARTESIA_API_KEY=your-cartesia-key
    export DAILY_API_KEY=your-daily-key
    export DAILY_ROOM_URL=https://your-domain.daily.co/your-room
    ```

    ### agent.py

    ```python
    """Pipecat voice agent with VoiceGateway cost metering and LLM fallback."""

    import asyncio
    import os

    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.openai_llm_context import (
        OpenAILLMContext,
    )
    from pipecat.services.cartesia.tts import CartesiaTTSService
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.transports.services.daily import DailyParams, DailyTransport

    import voicegateway


    async def main() -> None:
        transport = DailyTransport(
            os.environ["DAILY_ROOM_URL"],
            token=None,
            bot_name="VoiceBot",
            params=DailyParams(audio_out_enabled=True, vad_analyzer=SileroVADAnalyzer()),
        )

        stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])

        # guard() wraps the LLM with a fallback and a daily budget.
        # It returns a drop-in OpenAILLMService, so the Pipeline sees no difference.
        llm = voicegateway.guard(
            OpenAILLMService(
                api_key=os.environ["OPENAI_API_KEY"],
                model="gpt-4o-mini",
            ),
            fallback=[
                OpenAILLMService(
                    api_key=os.environ["OPENAI_API_KEY"],
                    model="gpt-4o",
                )
            ],
            rate_limit="60/min",
            budget="$5.00/day",
            project="my-agent",
        )

        tts = CartesiaTTSService(
            api_key=os.environ["CARTESIA_API_KEY"],
            voice_id="your-voice-id",
        )

        context = OpenAILLMContext(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a friendly voice assistant. Keep your answers short "
                        "and clear. One or two sentences unless asked for more."
                    ),
                }
            ]
        )
        context_aggregator = llm.create_context_aggregator(context)

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                context_aggregator.user(),
                llm,
                tts,
                transport.output(),
                context_aggregator.assistant(),
            ]
        )

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
        )

        # attach() is the single meter. Call it once, before runner.run(task).
        voicegateway.attach(task, project="my-agent")

        runner = PipelineRunner()
        await runner.run(task)


    if __name__ == "__main__":
        asyncio.run(main())
    ```

    ### Run the agent

    ```bash
    python agent.py
    ```

    Join your Daily room from a browser. After the call ends, the dashboard at
    `http://127.0.0.1:9090` shows a cost row per modality.

    ### What happens

    1. `DeepgramSTTService` transcribes speech. `attach()` accumulates audio bytes,
       converts them to minutes, and prices the STT.
    2. `guard(OpenAILLMService(...))` sends the transcript to GPT-4o mini. If it
       returns an error, `guard()` retries with GPT-4o automatically. `attach()`
       meters tokens and cost from the `LLMTokenUsage` frame.
    3. `CartesiaTTSService` synthesizes speech. `attach()` meters characters and cost.
    4. On pipeline end, `attach()` flushes all pending rows to the local SQLite sink
       and the dashboard updates.

    <Warning>
      Pipecat fallback switches providers before the first output frame only. If the
      primary service fails partway through a token stream, the error is surfaced
      rather than patched mid-stream. Size your fallback list for "primary is down
      or rejecting", not "primary died halfway".
    </Warning>
  </Tab>
</Tabs>

## View costs in the dashboard

```bash
voicegw dashboard
```

Opens your browser at `http://127.0.0.1:9090`. Cost rows appear in real time as
calls complete.

In the terminal:

```bash
voicegw costs    # tabular cost summary
voicegw logs     # recent request stream
voicegw status   # daemon and provider health
```

## Next steps

- [attach()](/guide/attach): full reference, including `tenant_id` for multi-tenant attribution.
- [guard()](/guide/guard): full reference, including per-framework fallback scope.
- [Core concepts](/guide/core-concepts): how attach, guard, projects, and sinks fit together.
- [Configuration reference](/configuration/voicegw-yaml): every YAML key.
- [Providers](/configuration/providers): all supported providers and model IDs.
