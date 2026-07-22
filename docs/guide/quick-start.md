---
title: Quick start
description: Install VoiceGateway, attach() it to a LiveKit or Pipecat agent, open the dashboard, and read your first per-call cost row in five minutes.
---

# Quick start

By the end of this guide you have VoiceGateway installed, `attach()` wired into a
minimal agent, and cost rows appearing in the dashboard.

## Prerequisites

- Python 3.11 or later
- A running LiveKit or Pipecat agent (or follow the agent skeleton below)
- API keys for at least one STT, LLM, and TTS provider

<Steps>
  <Step title="Install">
    Pick the extra for your framework, then bring the provider plugins your
    agent uses. VoiceGateway is framework-agnostic and does not bundle provider
    wheels: it meters your native instances by `model_id` through
    `voice-prices`.

    <Tabs>
      <Tab title="LiveKit">
        <CodeGroup>
        ```bash uv
        uv pip install "voicegateway[livekit]"
        uv pip install livekit-plugins-openai livekit-plugins-deepgram livekit-plugins-cartesia
        ```
        ```bash pip
        pip install "voicegateway[livekit]"
        pip install livekit-plugins-openai livekit-plugins-deepgram livekit-plugins-cartesia
        ```
        </CodeGroup>
      </Tab>
      <Tab title="Pipecat">
        <CodeGroup>
        ```bash uv
        uv pip install "voicegateway[pipecat]"
        uv pip install "pipecat-ai[openai,deepgram,cartesia]"
        ```
        ```bash pip
        pip install "voicegateway[pipecat]"
        pip install "pipecat-ai[openai,deepgram,cartesia]"
        ```
        </CodeGroup>
      </Tab>
    </Tabs>

    See [Installation](/guide/installation) for the full extras table, Docker,
    and source install.
  </Step>

  <Step title="Build a minimal agent">
    Create `agent.py`. Use your native framework providers exactly as you
    normally would.

    <Tabs>
      <Tab title="LiveKit">
        ```python
        # agent.py (LiveKit)
        from livekit.agents import Agent, AgentSession, WorkerOptions, cli
        from livekit.plugins import deepgram, openai, cartesia

        import voicegateway


        async def entrypoint(ctx):
            await ctx.connect()

            session = AgentSession(
                stt=deepgram.STT(model="nova-3"),
                llm=openai.LLM(model="gpt-4o-mini"),
                tts=cartesia.TTS(model="sonic-3"),
            )

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
        # agent.py (Pipecat)
        import os
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.services.deepgram.stt import DeepgramSTTService
        from pipecat.services.openai.llm import OpenAILLMService
        from pipecat.services.cartesia.tts import CartesiaTTSService

        import voicegateway


        async def main():
            stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])
            llm = OpenAILLMService(
                api_key=os.environ["OPENAI_API_KEY"],
                model="gpt-4o-mini",
            )
            tts = CartesiaTTSService(
                api_key=os.environ["CARTESIA_API_KEY"],
                voice_id="your-voice-id",
            )

            pipeline = Pipeline([stt, llm, tts])
            task = PipelineTask(
                pipeline,
                params=PipelineParams(
                    enable_metrics=True,
                    enable_usage_metrics=True,
                ),
            )

            voicegateway.attach(task, project="my-agent")

            runner = PipelineRunner()
            await runner.run(task)


        if __name__ == "__main__":
            import asyncio
            asyncio.run(main())
        ```

        <Note>
          `enable_metrics=True` and `enable_usage_metrics=True` are required on
          `PipelineParams`. Without them, Pipecat emits no usage frames and
          `attach()` has nothing to record.
        </Note>
      </Tab>
    </Tabs>
  </Step>

  <Step title="Open the dashboard">
    In a second terminal, start the dashboard:

    ```bash
    voicegw dashboard
    ```

    Your browser opens at `http://127.0.0.1:9090`. The Costs page is empty until
    your first call completes. Leave it open.
  </Step>

  <Step title="Run the agent and place a call">
    <Tabs>
      <Tab title="LiveKit">
        ```bash
        python agent.py dev
        ```

        Connect to your LiveKit room and say something. After the call, the
        dashboard Costs page shows a row per modality with the provider, model,
        usage units, and cost in USD.
      </Tab>
      <Tab title="Pipecat">
        ```bash
        python agent.py
        ```

        After the pipeline finishes, the dashboard Costs page shows a row per
        modality with the provider, model, usage units, and cost in USD.
      </Tab>
    </Tabs>
  </Step>

  <Step title="Read the cost rows">
    On the Costs page, each row shows:

    | Column | What it means |
    |---|---|
    | Modality | `stt`, `llm`, or `tts` |
    | Provider | `deepgram`, `openai`, `cartesia`, etc. |
    | Model | the model id passed to the plugin |
    | Usage | audio minutes (STT), tokens (LLM), characters (TTS) |
    | Cost | USD, priced through `voice-prices` |
    | Project | the `project=` argument you passed to `attach()` |

    In the terminal:

    ```bash
    voicegw costs    # tabular cost summary
    voicegw status   # daemon and provider health
    voicegw logs     # recent request stream
    ```
  </Step>
</Steps>

## Add a project budget

Multiple agents share one daemon? Give each its own project block in
`voicegw.yaml` to separate cost rows and daily budgets:

```yaml
projects:
  my-agent:
    name: My First Agent
    daily_budget: 5.00
    budget_action: warn

default_project: my-agent
```

Pass `project="my-agent"` to `attach()` (as shown above) and the rows are
attributed to that project automatically.

## Add fallback and rate limits

`guard()` wraps any native provider and returns a drop-in replacement. Use it
on the specific providers where you want control:

<Tabs>
  <Tab title="LiveKit">
    ```python
    import voicegateway
    from livekit.plugins import openai

    guarded_llm = voicegateway.guard(
        openai.LLM(model="gpt-4o-mini"),
        fallback=[openai.LLM(model="gpt-4o")],
        rate_limit="60/min",
        budget="$5.00/day",
    )

    session = AgentSession(stt=stt, llm=guarded_llm, tts=tts)
    voicegateway.attach(session, project="my-agent")
    ```
  </Tab>
  <Tab title="Pipecat">
    ```python
    import voicegateway
    from pipecat.services.openai.llm import OpenAILLMService

    guarded_llm = voicegateway.guard(
        OpenAILLMService(model="gpt-4o-mini"),
        fallback=[OpenAILLMService(model="gpt-4o")],
        rate_limit="60/min",
        budget="$5.00/day",
    )

    pipeline = Pipeline([stt, guarded_llm, tts])
    ```
  </Tab>
</Tabs>

`attach()` remains the single meter. `guard()` writes no metrics of its own.

## Next steps

- [First agent](/guide/first-agent): a complete worked agent file with `guard()`.
- [attach()](/guide/attach): full `attach()` reference, including `tenant_id` and fleet push.
- [guard()](/guide/guard): full `guard()` reference with fallback scope details.
- [Configuration reference](/configuration/voicegw-yaml): every YAML key.
