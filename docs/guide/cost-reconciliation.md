---
title: Cost reconciliation
description: Verify VoiceGateway's recorded costs against a provider invoice using voicegw export-costs and voicegw reconcile.
---
VoiceGateway records what it estimates you spent. Your provider records what they actually charged. These will not match exactly, by design: VoiceGateway estimates from per-request unit counts at a snapshot rate, while the provider bills against its authoritative meter and applies discounts, plan tiers, or post-hoc credits.

Expected drift is up to ~5% on LLM cost (rate sheets change often) and lower on STT/TTS, where the billing unit maps directly to what VoiceGateway records.

## When to reconcile

- Once in the first 30 days after deployment, to catch setup errors before they accumulate.
- After a provider rate change, to confirm the catalog refreshed in time.
- Before sending invoices that pass AI cost through to clients or internal teams.
- Whenever VoiceGateway's number diverges from a provider dashboard by more than 5%.

This is a spot-check and incident-response flow, not a per-billing-period requirement.

## Supported providers

`voicegw reconcile` supports exactly three providers today, each tied to one modality: **openai** (LLM, unit `tokens`), **deepgram** (STT, unit `audio_s`), **cartesia** (TTS, unit `chars`). No other provider (including Anthropic, Groq, or ElevenLabs) is currently supported.

## Prerequisites

- VoiceGateway recording to SQLite (`storage.path` set in `voicegw.yaml`).
- A provider usage export for the same window, converted to VoiceGateway's format. See [Reconcile file formats](/reference/reconcile-formats).

## Workflow

<Steps>
  <Step title="Pull the VoiceGateway side (optional)">
    ```bash
    voicegw export-costs --start 2026-05-01 --end 2026-05-31 --format csv > vg-may-2026.csv
    ```
    `reconcile` reads the database directly, so this step is only a sanity check before diffing.
  </Step>
  <Step title="Convert the provider export">
    Each provider's dashboard exposes a usage export in its own shape. Convert it once with the per-provider snippet on [Reconcile file formats](/reference/reconcile-formats):

    ```bash
    # Download the CSV from platform.openai.com/usage for the period.
    python convert-openai.py openai-may-2026.csv openai-vg-format-may-2026.csv
    ```

    Deepgram: `console.deepgram.com/usage`. Cartesia: `play.cartesia.ai`.
  </Step>
  <Step title="Run reconcile">
    ```bash
    voicegw reconcile \
      --provider openai \
      --start 2026-05-01 --end 2026-05-31 \
      --provider-usage-file openai-vg-format-may-2026.csv
    ```

    ```text
    Model             VG tokens  Provider tokens    Δ%   VG cost  Prov cost       Δ$     Δ%
    -----------------------------------------------------------------------------------------
    gpt-4o-mini        1500000.0        1500000.0 +0.00%  $0.0225   $0.0225  $+0.000  +0.00%
    gpt-4o              250000.0         260000.0 +3.85%  $1.2500   $1.3000  $+0.050  +3.85%
    ```

    `--format csv` or `--format json` give machine-readable output. `--threshold` (default `5.0`) sets the cost-diff percent that flags a row.
  </Step>
  <Step title="Interpret the diff">
    | Modality | Expected | Investigate at |
    |---|---|---|
    | LLM | within ~5% | more than 5% on cost, or any % on units |
    | STT | within ~1% | more than 2% on cost, or any % on units |
    | TTS | within ~2% | more than 3% on cost, or any % on units |

    Unit-side drift means VoiceGateway missed or miscounted events. Cost-side-only drift means the rate sheet moved.
  </Step>
</Steps>

## Interpreting specific patterns

**Units agree, cost diverges.** `voice-prices` hasn't caught up to a rate change, or your account has a non-public discount. Try:

```bash
uv pip install --upgrade voice-prices
```

**Units disagree.** Either a streaming request dropped before VoiceGateway saw its usage event (check `voicegw logs` for `failed to record cost` / `incomplete usage`), or the provider bills a unit VoiceGateway's catalog doesn't split the same way (e.g. realtime vs. pre-recorded audio).

**A model appears on only one side.** `(no provider data)` means the provider's billing dashboard lags (wait 24-72h and re-pull) or VoiceGateway routed to the wrong model id. `(no vg data)` usually means another client is sharing the same API key.

## Why estimate instead of mirror

Provider billing APIs often lag the request by 24-72 hours, so a real-time dashboard needs its own fast estimate. Maintaining seven providers' billing APIs inside VoiceGateway is not worth the tax when reconciliation already exists as the audit step. Use VoiceGateway for real-time observability; reconcile against the invoice when a number needs to be exact.

## See also

<CardGroup>
  <Card title="voicegw reconcile" href="/cli/reconcile">
    Full CLI flag reference.
  </Card>
  <Card title="voicegw export-costs" href="/cli/export-costs">
    Inspect VoiceGateway's per-request rows directly.
  </Card>
  <Card title="Reconcile file formats" href="/reference/reconcile-formats">
    Per-provider schema and conversion snippets.
  </Card>
  <Card title="voicegw costs" href="/cli/costs">
    Quick summary without a provider comparison.
  </Card>
</CardGroup>
