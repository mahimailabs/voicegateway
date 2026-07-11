---
title: Rating
description: How VoiceGateway turns recorded provider cost into a billable price using a configurable rate card, stamps that price immutably onto every request row, and exposes it through the billing API and the voicegw prices commands.
---

VoiceGateway records what a request cost (see [Cost Tracking](/architecture/cost-tracking)). The rating layer turns that recorded cost into a billable price: it resolves a rate card, applies either a cost-plus markup or a fixed advertised rate, and stamps the result onto the request row as `rated_price_usd` plus an audit token `rate_rule`. This is the layer that lets a biller like ShipVoice keep Stripe, invoices, and credits while VoiceGateway produces the rated usage those invoices are built from.

This page covers the rating subsystem: the rate card, how a rule is resolved, where rating runs, and how the rated numbers feed the billing API and the `voicegw prices` commands.

## The rate card

A rate card is a global `default_markup` fallback plus an ordered list of rules. Each rule is scoped on two axes and carries exactly one kind of arithmetic. It is configured under `rate_card:` in `voicegw.yaml` (see the [config reference](/configuration/voicegw-yaml)).

**Scope fields** (all optional, default "any"): `modality`, `provider`, `model`, `tenant`, `plan`. A rule with no scope fields matches every request.

**Rule kind** is one of two:

- **cost_plus** (`markup`): the billable price is the recorded provider cost multiplied by `markup`. Because it multiplies the recorded cost, a cost-plus rule auto-follows voice-prices base movement: when the base price changes, the rated price tracks it with no edit.
- **fixed** (`fixed` + `unit`): the billable price is an advertised `$/unit` multiplied by the request's billable quantity in that unit. A fixed rule is decoupled from the base cost, so it holds a stable advertised price even as the base moves. Valid units: `minute`, `second`, `char`, `1k_char`, `token`, `1k_token`, `1m_token`, `request`.

## How a rule is resolved

Rating a request resolves the single most specific matching rule. Specificity ranks tenant over plan over global, and model over provider over modality-only:

`tenant > plan > global`, and within that `model > provider > modality-only`.

Among rules that tie on specificity, the one that appears later in the list wins. That ordering is deliberate: a DB override layered after the YAML seed takes precedence over the seed rule it shadows. When no rule matches, the request falls back to a cost-plus pass at the card's `default_markup`.

## Write-time, immutable rating

Rating happens at write time, once, on the same path that records cost. Every request row stores two fields:

| Field | Type | Meaning |
|-------|------|---------|
| `rated_price_usd` | `float` | The billable price for this request. |
| `rate_rule` | `str` | An audit token naming the rule that produced the price. |

The `rate_rule` token is human-readable and self-documenting: `cost_plus:1.3` (recorded cost times 1.3), `fixed:0.006/minute` (an advertised fixed rate), or `default:1` (no rule matched, default markup applied). Because the price and its rule are stamped at write time, they are immutable: editing the card later never rewrites historical rows. Yesterday's usage stays billed at yesterday's card, which is what makes the rated numbers safe to invoice against.

## Where rating runs

Rating runs server-side, in the gateway's cost-tracking middleware, when you run `voicegw serve`. The active card is the `rate_card:` seed plus any DB overrides, merged at startup and on every config refresh, and applied as each request row is written. If rating ever fails, it falls back to a cost pass-through so the row is still recorded with a billable price.

The agent-side `attach()` path stays a cost pass-through by design. Margins are a server-side concern: an agent process records raw cost, and the server (or a hosted cloud that rates on ingest) applies the card. The rating logic lives in the pure `voicegateway.billing` module, so a hosted cloud can import it and rate on ingest without pulling in the rest of the gateway.

<Note>
A self-hosted collector re-rates fleet rows on ingest. Rows pushed to `POST /v1/ingest` arrive unrated (agents rate at pass-through), so the collector rates each one against its own card, using the tenant resolved from the verified `vk_` key, and overwrites any agent-supplied rated price (agents are not trusted with margins). Both the SQLite and ClickHouse sinks store `rated_price_usd` / `rate_rule`, and `GET /v1/billing/usage` reads from whichever store the collector uses (ClickHouse when configured, SQL otherwise). The `voicegw prices reconcile` CLI still reads the SQL store directly, so on a ClickHouse-backed collector use the HTTP endpoint for accurate margins.
</Note>

## How rated usage is consumed

Once every row carries `rated_price_usd`, two surfaces read it:

- **Billing API.** `GET /v1/billing/usage` rolls rated revenue, recorded cost, and margin up per tenant for a window; passing `tenant` adds per-(modality, model) line items for invoice detail. `GET /v1/billing/rate-card` returns the card in effect. See the [HTTP API reference](/api/http-api).
- **`voicegw prices` commands.** `voicegw prices ls` prints the effective card, `voicegw prices reconcile` flags tenants with thin or negative margins over a window, `voicegw prices sync` checks fixed rules against the current base cost, and `voicegw prices set` / `rm` edit the DB overrides. See [voicegw prices](/cli/prices).

<Note>
The rate card is one store with two surfaces: the `rate_card:` seed in `voicegw.yaml` plus DB overrides. `voicegw prices set` / `rm` edit the DB overrides (one rule per scope, keyed by `tenant|plan|modality|provider|model`), and a DB override wins a specificity tie against the seed rule it shadows. A PUT on `/v1/billing/rate-card` and a dashboard editor over the same store are follow-ups.
</Note>

## Where to find each piece

| Component | Path |
|-----------|------|
| Rate card (data + resolution) | `src/voicegateway/billing/rate_card.py` |
| Rating arithmetic | `src/voicegateway/billing/rating.py` |
| Margin reconcile + price sync | `src/voicegateway/billing/reconcile.py` |
| Billing API | `src/voicegateway/server/api/billing.py` |
| `voicegw prices` command group | `src/voicegateway/cli/prices_cli.py` |
| Rate card config schema | `src/voicegateway/schemas/config_schema.py` |
