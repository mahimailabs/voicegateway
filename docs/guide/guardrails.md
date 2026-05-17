# Voice-specific guardrails

VoiceGateway provides project-scoped, LLM-side guardrails for voice agents. Guardrails are injected through the existing `voicegateway.inference.LLM(...)` drop-in path, so agent code keeps the same LiveKit construction pattern.

Guardrails do not create a proxy session service, do not inspect raw audio, and do not intercept arbitrary tool calls. They append a versioned system prompt block to the LiveKit chat context and register one reserved LiveKit function tool named `report_guardrail_action`.

## Policy model

Guardrail policies live per project. The default is disabled, with every category set to `off`.

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

Categories:

- `pii`
- `financial`
- `medical`
- `prompt_injection`
- `off_topic`

Actions:

- `redact`: answer without repeating the sensitive detail.
- `block`: decline the current turn with a brief, neutral response.
- `alert`: continue normally and write an audit event.
- `off`: disable that category.

## Runtime behavior

On the first guarded LLM chat in a session, VoiceGateway freezes the active project policy. Later dashboard or API edits affect new sessions only.

When guardrails are active:

- VoiceGateway appends a `<voicegateway_guardrails version="v0.6.0">` block after existing system/developer instructions.
- VoiceGateway registers `report_guardrail_action(category, action, context_excerpt)`.
- A user-defined tool with the same name is rejected for that session.
- Audit rows are written to `guardrail_events` with `event_type = fired`.

Session detail responses include:

- `guardrails_active`
- `guardrails_bypassed`
- `guardrail_policy_snapshot`
- `guardrail_events`

This lets the dashboard distinguish "active policy, zero events" from "no guardrail audit".

## Bypass

Use bypass only for trusted internal sessions where the operator intentionally wants no injection. VoiceGateway records a bypass audit event when the frozen policy would otherwise be active.

```python
from voicegateway import inference

session_id = inference.start_session(bypass_guardrails=True)

# Or, when binding a custom LiveKit AgentSession:
inference.attach_session(agent_session, bypass_guardrails=True)
```

Bypass skips prompt/tool injection for the session. The bypass row has `event_type = bypassed`; `category` and `action` are `NULL`.

## CLI

`voicegw guardrails` talks to the dashboard API:

```bash
voicegw guardrails show --project support
voicegw guardrails set --project support --category pii --action redact
voicegw guardrails clear --project support
voicegw guardrails dry-run --project support
```

Use `VOICEGW_API_KEY` when your dashboard API requires auth.

## API

Server API:

- `GET /v1/projects/{id}/guardrails`
- `POST /v1/projects/{id}/guardrails`
- `GET /v1/guardrails/events`
- `GET /v1/guardrails/aggregate`

Dashboard API mirrors these under `/api/...`.

Aggregates count only `fired` rows. Event listings can include both `fired` and `bypassed`.

## Caveats

These guardrails are prompt-side controls, not a deterministic safety classifier. They depend on the selected LLM following instructions and calling the reserved tool. Use provider-native moderation, contractual compliance review, and invoice/log reconciliation for higher-assurance workflows.
