---
title: Quickstart
description: Install VoiceGateway next to an existing LiveKit or Pipecat agent, call attach() once, and read your first per-call cost row in the dashboard.
---

# Quickstart

By the end of this page you have VoiceGateway installed, one `attach()` call in your
agent, and a per-modality cost row on screen for a real call.

This is the agent layer. The SFU and SIP layers have different prerequisites and are
covered in [What you need](/guide/prerequisites).

## Before you start

- Python 3.11 or later
- API keys for one STT, one LLM, and one TTS provider
- **LiveKit only:** a LiveKit project. The agent worker cannot start without one, and there is no way around this. A free LiveKit Cloud project or a local `livekit-server --dev` both work.

<Steps>
  <Step title="Install">
    Install the extra for your framework, then the provider plugins your agent
    uses. VoiceGateway bundles no provider wheels; it meters the native instances
    you already construct.

    <Tabs>
      <Tab title="LiveKit">
        ```bash
        pip install "voicegateway[livekit,dashboard]"
        pip install "livekit-agents[silero]"
        pip install livekit-plugins-openai livekit-plugins-deepgram livekit-plugins-cartesia
        ```
      </Tab>
      <Tab title="Pipecat">
        ```bash
        pip install "voicegateway[pipecat,dashboard]"
        pip install "pipecat-ai[openai,deepgram,cartesia,daily]"
        ```
      </Tab>
    </Tabs>

    The `dashboard` extra pulls FastAPI and uvicorn. Without it `voicegw serve`
    in step 4 exits and tells you to install it. See
    [Installation](/guide/installation) for the full extras table, Docker, and
    source builds.
  </Step>

  <Step title="Set your environment variables">
    <Tabs>
      <Tab title="LiveKit">
        ```bash
        # Required. The agent worker connects to LiveKit before it does anything else.
        export LIVEKIT_URL=wss://your-project.livekit.cloud
        export LIVEKIT_API_KEY=your-livekit-key
        export LIVEKIT_API_SECRET=your-livekit-secret

        # Read implicitly by the plugin classes below.
        export DEEPGRAM_API_KEY=your-deepgram-key
        export OPENAI_API_KEY=your-openai-key
        export CARTESIA_API_KEY=your-cartesia-key
        ```

        Running `livekit-server --dev` locally instead? Use
        `LIVEKIT_URL=ws://localhost:7880`, `LIVEKIT_API_KEY=devkey`,
        `LIVEKIT_API_SECRET=secret`.
      </Tab>
      <Tab title="Pipecat">
        ```bash
        export DEEPGRAM_API_KEY=your-deepgram-key
        export OPENAI_API_KEY=your-openai-key
        export CARTESIA_API_KEY=your-cartesia-key
        ```

        Pipecat has no LiveKit dependency, so no `LIVEKIT_*` variables are needed
        unless you use a LiveKit transport.
      </Tab>
    </Tabs>
  </Step>

  <Step title="Add attach() to your agent">
    One call, placed before the session or pipeline starts. Every native provider
    stays exactly as it is.

    <Tabs>
      <Tab title="LiveKit">
        ```python
        # agent.py
        from livekit.agents import Agent, AgentSession, WorkerOptions, cli
        from livekit.plugins import cartesia, deepgram, openai

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

            await session.start(
                agent=Agent(instructions="You are a helpful voice assistant."),
                room=ctx.room,
            )


        if __name__ == "__main__":
            cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
        ```
      </Tab>
      <Tab title="Pipecat">
        ```python
        # agent.py
        import asyncio
        import os

        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.services.cartesia.tts import CartesiaTTSService
        from pipecat.services.deepgram.stt import DeepgramSTTService
        from pipecat.services.openai.llm import OpenAILLMService

        import voicegateway


        async def main():
            pipeline = Pipeline([
                DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"]),
                OpenAILLMService(
                    api_key=os.environ["OPENAI_API_KEY"],
                    model="gpt-4o-mini",
                ),
                CartesiaTTSService(
                    api_key=os.environ["CARTESIA_API_KEY"],
                    voice_id="your-voice-id",
                ),
            ])
            task = PipelineTask(
                pipeline,
                params=PipelineParams(
                    enable_metrics=True,
                    enable_usage_metrics=True,
                ),
            )

            # One call. Every STT / LLM / TTS request is metered from here.
            voicegateway.attach(task, project="my-agent")

            await PipelineRunner().run(task)


        if __name__ == "__main__":
            asyncio.run(main())
        ```

        <Warning>
          `enable_metrics=True` and `enable_usage_metrics=True` are required.
          Without them Pipecat emits no usage frames, `attach()` records nothing,
          and the dashboard stays empty with no error to tell you why.
        </Warning>
      </Tab>
    </Tabs>
  </Step>

  <Step title="Start the daemon">
    In a second terminal:

    ```bash
    voicegw init     # writes voicegw.yaml; the daemon refuses to start without one
    voicegw serve    # serves the API and the dashboard on http://localhost:8080
    ```

    `voicegw init` writes a minimal config with `cost_tracking` already enabled,
    pointing at the same default database `attach()` writes to. That is what
    connects your agent to the dashboard with no further wiring.

    Open `http://localhost:8080` in a browser. The Costs page is empty until the
    first call completes. Leave it open.

    <Note>
      `voicegw dashboard` does **not** start anything. It reads `serve.host` and
      `serve.port` from your config and opens a browser at that address, so it is
      a convenience once the daemon is already running. Add `--no-open` to print
      the URL instead, which is what you want over SSH.
    </Note>
  </Step>

  <Step title="Run the agent and place a call">
    <Tabs>
      <Tab title="LiveKit">
        ```bash
        python agent.py dev
        ```

        Now get audio into the room. The quickest route is the
        [LiveKit Playground](https://playground.livekit.io), which connects to your
        project and gives you a microphone. Say something, then end the call.
      </Tab>
      <Tab title="Pipecat">
        ```bash
        python agent.py
        ```

        Speak through whichever transport you wired into the pipeline. Rows are
        written as the pipeline runs and finalized when it ends.
      </Tab>
    </Tabs>
  </Step>

  <Step title="Read the cost rows">
    Refresh the dashboard and open the Costs page. It aggregates by model:

    | Column | What it means |
    |---|---|
    | Model | the model id your plugin reported |
    | Requests | how many calls hit that model |
    | Cost | USD, priced through `voice-prices` |
    | Pricing source | which catalog entry produced that number |

    For the per-request view, with the modality, provider, usage units, and latency of
    each individual call, use the terminal:

    ```bash
    voicegw logs     # one row per request: modality, provider, model, usage, cost
    voicegw costs    # totals per provider, project, and period
    voicegw status   # daemon and provider health
    ```
  </Step>
</Steps>

## Nothing showed up?

| Symptom | Cause |
|---|---|
| LiveKit worker exits at startup with a `ValueError` | `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` unset. The worker validates these before it runs your entrypoint |
| Call connects, dashboard stays empty (Pipecat) | `enable_metrics` / `enable_usage_metrics` not set on `PipelineParams` |
| `voicegw serve` says dashboard dependencies are not installed | install the `dashboard` extra |
| `ConfigError: No voicegw.yaml found` | run `voicegw init` first. `attach()` needs no config file, but the daemon does |
| Browser cannot connect after `voicegw dashboard` | nothing is serving. `voicegw dashboard` opens a URL, it does not start the daemon |
| Rows appear with zero cost | a local model (`local/*`, `ollama/*`), which is priced at zero by design |

More cases in [Troubleshooting](/reference/troubleshooting).

## Next

<CardGroup cols={2}>
  <Card title="What you can profile" icon="layer-group" href="/guide/what-you-can-profile">
    The agent layer is one of three. See the SFU and SIP layers.
  </Card>
  <Card title="First agent" icon="code" href="/guide/first-agent">
    A complete worked agent file with `attach()` and a `guard()` fallback.
  </Card>
  <Card title="attach()" icon="eye" href="/guide/attach">
    Full signature: projects, tenants, channels, and fleet push.
  </Card>
  <Card title="guard()" icon="shield" href="/guide/guard">
    Add fallback chains, rate limits, and daily spend caps.
  </Card>
</CardGroup>
