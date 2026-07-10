---
title: Get started (Self-Host)
description: Install VoiceGateway alongside your existing LiveKit or Pipecat agent, call attach() once, and see per-call costs in the dashboard in under five minutes.
---

# Get started

VoiceGateway plugs into a LiveKit or Pipecat agent you already have. You keep
your native provider plugins. VoiceGateway adds one `attach()` call that meters
every STT, LLM, and TTS request and writes the costs to a local dashboard.

<Steps>
  <Step title="Install the extra for your framework">
    <CodeGroup>
    ```bash uv
    # LiveKit Agents
    uv pip install "voicegateway[livekit]"

    # Pipecat
    uv pip install "voicegateway[pipecat]"
    ```
    ```bash pip
    # LiveKit Agents
    pip install "voicegateway[livekit]"

    # Pipecat
    pip install "voicegateway[pipecat]"
    ```
    </CodeGroup>

    Python 3.11 or later is required. See [Installation](/guide/installation) for
    provider extras and Docker options.
  </Step>

  <Step title="Attach to your existing agent">
    Add `import voicegateway` and one `attach()` call. Keep every native provider
    plugin exactly as it is.

    <Tabs>
      <Tab title="LiveKit">
        ```python
        from livekit.agents import Agent, AgentSession
        from livekit.plugins import deepgram, openai, cartesia

        import voicegateway


        async def entrypoint(ctx):
            await ctx.connect()

            session = AgentSession(
                stt=deepgram.STT(model="nova-3"),
                llm=openai.LLM(model="gpt-4o-mini"),
                tts=cartesia.TTS(model="sonic-3"),
            )

            # One call. Every STT / LLM / TTS request is metered from here.
            voicegateway.attach(session, project="my-agent")

            await session.start(agent=Agent(instructions="Be helpful."), room=ctx.room)
        ```
      </Tab>
      <Tab title="Pipecat">
        ```python
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.services.deepgram.stt import DeepgramSTTService
        from pipecat.services.openai.llm import OpenAILLMService
        from pipecat.services.cartesia.tts import CartesiaTTSService

        import voicegateway

        stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])
        llm = OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"], model="gpt-4o-mini")
        tts = CartesiaTTSService(api_key=os.environ["CARTESIA_API_KEY"], voice_id="your-voice-id")

        pipeline = Pipeline([transport.input(), stt, llm, tts, transport.output()])
        task = PipelineTask(
            pipeline,
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        )

        # One call. Every STT / LLM / TTS request is metered from here.
        voicegateway.attach(task, project="my-agent")
        ```

        <Note>
          Pipecat requires `enable_metrics=True` and `enable_usage_metrics=True` on
          `PipelineParams`. Without these flags, Pipecat emits no usage frames and
          `attach()` has nothing to record.
        </Note>
      </Tab>
    </Tabs>
  </Step>

  <Step title="Run the dashboard">
    ```bash
    voicegw dashboard
    ```

    That opens your browser at `http://127.0.0.1:9090`. The dashboard shows live
    cost rows the moment your first call completes. Use `--no-open` to print the
    URL without launching a browser (useful over SSH).
  </Step>

  <Step title="See the first call">
    Run your agent as normal, place a call, and refresh the dashboard. Each row
    shows the modality (STT / LLM / TTS), provider, model, usage units, and the
    cost in USD.

    In the terminal:

    ```bash
    voicegw costs    # tabular cost summary
    voicegw status   # daemon and provider health
    voicegw logs     # recent request stream
    ```
  </Step>
</Steps>

<Tip>
  Want to add fallback providers or enforce a daily spend cap? `guard()` wraps any
  native provider and returns a drop-in replacement. See [guard()](/guide/guard).
</Tip>

---

## What's next

<CardGroup cols={2}>
  <Card title="Installation" icon="download" href="/guide/installation">
    Full install matrix: uv, pip, framework extras, provider extras, Docker.
  </Card>
  <Card title="Quick start" icon="bolt" href="/guide/quick-start">
    Five-minute path from install to reading your first per-call cost row.
  </Card>
  <Card title="First agent" icon="code" href="/guide/first-agent">
    A complete worked agent file with attach() and a guard() fallback example.
  </Card>
  <Card title="Core concepts" icon="book" href="/guide/core-concepts">
    attach(), guard(), projects, sinks, and how the pieces fit together.
  </Card>
</CardGroup>
