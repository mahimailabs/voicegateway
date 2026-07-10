---
title: Voice-specific guardrails
description: Project-scoped, LLM-side guardrails for voice agents, injected through the attach() path.
---

# Voice-specific guardrails

VoiceGateway provides project-scoped, LLM-side guardrails for voice agents. Guardrails are injected through the existing `attach()` seam, so your agent code keeps the same native provider construction pattern.

Guardrails do not create a proxy session service, do not inspect raw audio, and do not intercept arbitrary tool calls. They append a versioned system prompt block to the chat context and register one reserved function tool named `report_guardrail_action`.

<Note>
Guardrails are prompt-side controls, not a deterministic safety classifier. They depend on the selected LLM following instructions and calling the reserved tool. Use provider-native moderation, contractual compliance review, and reconciliation for higher-assurance workflows.
</Note>

## Policy model

Guardrail policies live per project in `voicegw.yaml`. The default is disabled, with every category set to `off`.

```yaml
projects:
  support:
    name: Support Bot
    guardrails:
      enabled: true
      categories:
        pii: redact
        financial: block
        medical: alert
        prompt_injection: block
        off_topic: off
```

### Categories

- `pii`: personally identifiable information (names, phone numbers, account numbers).
- `financial`: financial advice, account balances, transaction details.
- `medical`: medical advice or diagnoses.
- `prompt_injection`: attempts to override or escape the system prompt.
- `off_topic`: requests outside the agent's declared scope.

### Actions

- `redact`: answer without repeating the sensitive detail.
- `block`: decline the current turn with a brief, neutral response.
- `alert`: continue normally and write an audit event.
- `off`: disable that category entirely.

## Wiring guardrails with attach()

Enable guardrails in `voicegw.yaml` for the project, then call `attach()` as normal. VoiceGateway detects the active policy and injects the guardrail block automatically on the first guarded LLM chat in the session.

<Tabs>
  <Tab title="LiveKit">
    ```python
    from livekit.agents import Agent, AgentSession, JobContext
    from livekit.plugins import deepgram, openai, cartesia

    from voicegateway import attach


    async def entrypoint(ctx: JobContext):
        await ctx.connect()

        session = AgentSession(
            stt=deepgram.STT(model="nova-3"),
            llm=openai.LLM(model="gpt-4o-mini"),
            tts=cartesia.TTS(model="sonic-3"),
        )

        # Guardrails are injected automatically because the "support" project
        # has guardrails.enabled: true in voicegw.yaml.
        attach(session, project="support")

        await session.start(
            agent=Agent(instructions="Be the Acme support agent."),
            room=ctx.room,
        )
    ```
  </Tab>
  <Tab title="Pipecat">
    ```python
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.services.cartesia.tts import CartesiaTTSService

    from voicegateway import attach


    async def run_agent():
        stt = DeepgramSTTService(api_key=DEEPGRAM_API_KEY)
        llm = OpenAILLMService(api_key=OPENAI_API_KEY, model="gpt-4o-mini")
        tts = CartesiaTTSService(api_key=CARTESIA_API_KEY, voice_id=VOICE_ID)

        pipeline = Pipeline([transport.input(), stt, llm, tts, transport.output()])
        task = PipelineTask(
            pipeline,
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        )

        # Guardrails are injected automatically because the "support" project
        # has guardrails.enabled: true in voicegw.yaml.
        attach(task, project="support")

        runner = PipelineRunner()
        await runner.run(task)
    ```
  </Tab>
</Tabs>

## Runtime behavior

On the first guarded LLM chat in a session, VoiceGateway freezes the active project policy. Later dashboard or API edits affect new sessions only.

When guardrails are active:

- VoiceGateway appends a `<voicegateway_guardrails version="v0.6.0">` block after existing system or developer instructions.
- VoiceGateway registers `report_guardrail_action(category, action, context_excerpt)` as a reserved function tool.
- A user-defined tool with the same name is rejected for that session.
- Audit rows are written to `guardrail_events` with `event_type = fired`.

Session detail responses include:

- `guardrails_active`
- `guardrails_bypassed`
- `guardrail_policy_snapshot`
- `guardrail_events`

This lets the dashboard distinguish "active policy, zero events" from "no guardrail audit".

## Bypass

Use bypass only for trusted internal sessions where you intentionally want no injection. VoiceGateway records a bypass audit event when the frozen policy would otherwise be active.

Pass `bypass_guardrails=True` as a keyword argument to `attach()`:

```python
from voicegateway import attach

# Trusted internal session: skip guardrail injection.
attach(session, project="support", bypass_guardrails=True)
```

Bypass skips prompt and tool injection for the session. The bypass row has `event_type = bypassed`; `category` and `action` are `NULL`.

## CLI

`voicegw guardrails` talks to the dashboard API:

```bash
voicegw guardrails show --project support
voicegw guardrails set --project support --category pii --action redact
voicegw guardrails clear --project support
voicegw guardrails dry-run --project support
```

Set `VOICEGW_API_KEY` when your dashboard API requires auth.

## HTTP API

- `GET /v1/projects/{id}/guardrails`
- `POST /v1/projects/{id}/guardrails`
- `GET /v1/guardrails/events`
- `GET /v1/guardrails/aggregate`

Dashboard API mirrors these under `/api/...`.

Aggregates count only `fired` rows. Event listings can include both `fired` and `bypassed`.

## See also

<CardGroup>
  <Card title="Guardrail prompts reference" href="/reference/guardrail-prompts">
    The exact prompt blocks VoiceGateway injects per category and action.
  </Card>
  <Card title="attach()" href="/guide/attach">
    Full signature and wiring reference for the attach() seam.
  </Card>
  <Card title="Configuration: projects" href="/configuration/projects">
    Set guardrail policies per project in voicegw.yaml.
  </Card>
  <Card title="guard()" href="/guide/guard">
    Active control: fallback, rate limiting, and spend caps.
  </Card>
</CardGroup>
