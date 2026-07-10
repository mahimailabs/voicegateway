---
title: Cost reconciliation
description: Verify VoiceGateway's recorded costs against your provider invoices using voicegw export-costs and voicegw reconcile.
---

# Cost reconciliation

VoiceGateway records what it estimates you spent. Your provider records what they actually charged. These numbers should be close. They will not match exactly, and that is by design: VoiceGateway estimates from per-request unit counts at a snapshot rate, while the provider bills against their authoritative meter and applies discounts, plan tiers, or post-hoc credits.

The expected drift is up to about 5% on LLM costs (because rate sheets change frequently) and lower on STT and TTS, where the unit of billing maps directly to what VoiceGateway records.

## When to reconcile

Run reconciliation:

- Once during the first 30 days after deployment, to catch setup errors (wrong rate sheet, miscounted units) before they accumulate.
- After a provider rate change, to confirm the catalog refreshed in time.
- Before sending invoices that aggregate AI costs to clients or internal teams.
- When VoiceGateway's number diverges from your dashboard by more than 5%.

You do not need to reconcile every billing period. The reconciliation flow is for spot-checks and incident response.

## Prerequisites

- VoiceGateway recording requests to a SQLite store (`storage.path` set in `voicegw.yaml`).
- A provider usage export covering the same time window. See [Reconcile file formats](/reference/reconcile-formats) for the per-provider schema VoiceGateway expects.
- The CLI on your PATH:

```bash
voicegw --version
```

## Workflow

<Steps>
  <Step title="Pull the VoiceGateway side">
    Inspect what VoiceGateway recorded before running the diff. This step is optional but useful for a quick sanity check.

    ```bash
    voicegw export-costs \
      --start 2026-05-01 --end 2026-05-31 \
      --format csv > vg-may-2026.csv
    ```

    `export-costs` writes one CSV row per request with timestamp, project, modality, provider, model, units, cost, pricing source, and status. Open it in any spreadsheet to spot-check unit counts before comparing.

    The `reconcile` command reads the same database directly, so this step is not required for the diff.
  </Step>
  <Step title="Pull and convert the provider export">
    Each provider's dashboard exposes a usage export. The exports are not in VoiceGateway's canonical format, so you convert once with a short Python snippet. The conversion scripts are documented per-provider on the [Reconcile file formats](/reference/reconcile-formats) page.

    For OpenAI:

    ```bash
    # Download the CSV from platform.openai.com/usage for the period.
    # Run the conversion snippet from reconcile-formats.
    python convert-openai.py \
      openai-may-2026.csv \
      openai-vg-format-may-2026.csv
    ```

    For Deepgram, download from `console.deepgram.com/usage`. For Cartesia, download from `play.cartesia.ai`.
  </Step>
  <Step title="Run reconcile">
    ```bash
    voicegw reconcile \
      --provider openai \
      --start 2026-05-01 --end 2026-05-31 \
      --provider-usage-file openai-vg-format-may-2026.csv
    ```

    Default output is a text table:

    ```text
    Model             VG tokens  Provider tokens    Δ%   VG cost  Prov cost       Δ$     Δ%
    -----------------------------------------------------------------------------------------
    gpt-4o-mini        1500000.0        1500000.0 +0.00%  $0.0225   $0.0225  $+0.000  +0.00%
    gpt-4o              250000.0         260000.0 +3.85%  $1.2500   $1.3000  $+0.050  +3.85%
    gpt-4o-staging      100000.0               0  +0.00%  $0.0050   $0.0000  $-0.005  +0.00% (no provider data)
    ```

    For machine-readable output, pass `--format csv` or `--format json`. JSON is useful when piping into a monitoring or alerting tool.
  </Step>
  <Step title="Interpret the diff">
    Three columns carry the most signal:

    - **Delta percent on units.** How far off VoiceGateway's unit count is from the provider's. This should be near zero. A non-zero unit-side diff means VoiceGateway missed events or counted differently than the provider.
    - **Delta dollar on cost.** Absolute dollar gap between VoiceGateway's calculation and the provider's invoice line.
    - **Delta percent on cost.** The headline number. See the tolerance table below.

    | Modality | Expected | Investigate at |
    |---|---|---|
    | LLM | within ~5% | more than 5% on cost, or any % on units |
    | STT | within ~1% | more than 2% on cost, or any % on units |
    | TTS | within ~2% | more than 3% on cost, or any % on units |

    LLM has wider tolerance because its rate sheet changes frequently. `voice-prices` tracks published changes, but a same-day reconcile after a price change can show several percent of drift until the catalog is refreshed and the pin is bumped.
  </Step>
</Steps>

## Interpreting specific patterns

### Units agree but cost diverges

The provider's per-model rate has drifted relative to what VoiceGateway calculated. For LLM costs this means `voice-prices` has not yet caught up to the rate change, or your account has a discount (volume tier, BAA tier) the public catalog does not know about.

Update `voice-prices` and re-run:

```bash
uv pip install --upgrade voice-prices
# pip install --upgrade voice-prices
```

If the gap persists after upgrading, your account is on a non-public rate and the gap reflects the discount you are receiving.

### Units disagree

VoiceGateway is counting differently than the provider regardless of cost. Two common causes:

1. **Missed events.** A streaming request dropped before VoiceGateway saw the `usage_collected` event. Check for warnings in `voicegw logs` matching `failed to record cost` or `incomplete usage`.
2. **Unit-of-billing mismatch.** The provider bills realtime audio differently from pre-recorded audio (Deepgram), or audio tokens differently from text tokens (OpenAI), and VoiceGateway's catalog or model IDs do not split them correctly.

### A model appears on only one side

`(no provider data)` means VoiceGateway logged requests for a model but the provider's invoice has no line for it. Either the provider's billing dashboard lags by 24-72 hours (wait and re-pull), or VoiceGateway is routing requests to the wrong model id.

`(no vg data)` means the provider charged for a model VoiceGateway did not record. Usually this means a non-VoiceGateway client is sharing the same API key. Check whether another system is hitting the API with the same credentials.

## Why VoiceGateway estimates instead of mirroring

VoiceGateway uses fast estimates rather than pulling authoritative invoice data for three reasons:

1. **Provider billing APIs lag the request.** Several providers do not surface per-request cost until 24-72 hours after the request. Real-time cost dashboards need an immediate number.
2. **Maintenance cost.** Each provider's billing API has different auth, format, and rate-limit shape. Maintaining seven of them inside VoiceGateway is an ongoing tax.
3. **Reconciliation is the audit anyway.** The right model is "VoiceGateway gives you a fast, defensible estimate; you reconcile when it matters."

If your billing requirements are FinOps-grade (every dollar must match the invoice for accounting purposes), use VoiceGateway for real-time observability and reconcile against the provider invoice for the official number.

## See also

<CardGroup>
  <Card title="voicegw reconcile" href="/cli/reconcile">
    Full CLI flag reference for the reconcile command.
  </Card>
  <Card title="voicegw export-costs" href="/cli/export-costs">
    Inspect VoiceGateway's per-request rows directly.
  </Card>
  <Card title="Reconcile file formats" href="/reference/reconcile-formats">
    Per-provider schema and conversion snippets.
  </Card>
  <Card title="voicegw costs" href="/cli/costs">
    Quick cost summary without a provider comparison.
  </Card>
</CardGroup>
