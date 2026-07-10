---
title: Voice-specific guardrails
description: Project-scoped, LLM-side guardrails for voice agents. Configure categories and actions per project in voicegw.yaml; VoiceGateway injects a versioned guardrail block into the LLM chat context at runtime.
---

# Voice-specific guardrails

VoiceGateway provides project-scoped, LLM-side guardrails for voice agents. A
guardrail policy appends a versioned system-prompt block to the chat context and
registers one reserved function tool named `report_guardrail_action`, so the
model can flag when a category fires.

Guardrails do not create a proxy session service, do not inspect raw audio, and
do not intercept arbitrary tool calls. They are prompt-side controls.

<Note>
Guardrails are prompt-side controls, not a deterministic safety classifier. They
depend on the selected LLM following instructions and calling the reserved tool.
Use provider-native moderation, contractual compliance review, and reconciliation
for higher-assurance workflows.
</Note>

## Policy model

Guardrail policies live per project in `voicegw.yaml`. The default is disabled,
with every category set to `off`.

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

## How guardrails are applied

Guardrail injection happens inside VoiceGateway's instrumented LLM wrapper. When a
project has an enabled policy, that wrapper rewrites the chat context on the first
guarded LLM turn: it appends the guardrail block and registers the reserved tool
before delegating to the underlying provider.

<Warning>
Guardrail injection runs through the instrumented LLM path (the
`voicegateway.LLM(...)` provider wrapper). The passive [`attach()`](/guide/attach)
seam meters cost and latency but does not modify prompts, so `attach()` alone does
not inject guardrails. [`guard()`](/guide/guard) is control-only (fallback, rate
limiting, budgets) and does not inject them either. Route your LLM through the
gateway's LLM wrapper for the policy to take effect. If your agent uses only
native providers plus `attach()`, the policy is recorded as active but no block is
injected.
</Warning>

This is a known gap: guardrail injection is not yet wired into the `attach()` /
`guard()` seams. See [the migration guide](/guide/migration-attach-guard) for how
the instrumented wrapper relates to the current API.

## Runtime behavior

On the first guarded LLM chat in a session, VoiceGateway freezes the active
project policy. Later dashboard or API edits affect new sessions only.

When guardrails are active:

- VoiceGateway appends a `voicegateway_guardrails` block (version `v0.6.0`) after
  existing system or developer instructions.
- VoiceGateway registers `report_guardrail_action(category, action, context_excerpt)`
  as a reserved function tool.
- A user-defined tool with the same name is rejected for that session.
- Audit rows are written to `guardrail_events` with `event_type = fired`.

Session detail responses include:

- `guardrails_active`
- `guardrails_bypassed`
- `guardrail_policy_snapshot`
- `guardrail_events`

This lets the dashboard distinguish "active policy, zero events" from "no
guardrail audit".

## Disabling and bypass

To turn a policy off, set `enabled: false` for the project, or set individual
categories to `off`. Both take effect for new sessions (the policy is frozen per
session at the first guarded turn).

A per-session bypass path exists internally for trusted sessions and writes a
`bypassed` audit event when the frozen policy would otherwise be active. It is not
part of the public `attach()` / `guard()` surface; use the policy config above to
control guardrails from your agent.

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

The dashboard API mirrors these under `/api/...`.

Aggregates count only `fired` rows. Event listings can include both `fired` and
`bypassed`.

## See also

<CardGroup>
  <Card title="Guardrail prompts reference" href="/reference/guardrail-prompts">
    The exact prompt blocks VoiceGateway injects per category and action.
  </Card>
  <Card title="Configuration: projects" href="/configuration/projects">
    Set guardrail policies per project in voicegw.yaml.
  </Card>
  <Card title="attach()" href="/guide/attach">
    The passive metering seam (does not inject guardrails).
  </Card>
  <Card title="Migration guide" href="/guide/migration-attach-guard">
    How the instrumented LLM wrapper relates to the current API.
  </Card>
</CardGroup>
