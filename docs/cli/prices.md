---
title: voicegw prices
description: Inspect the billing rate card, reconcile rated revenue against recorded cost per tenant, and sync fixed-price rules against the current base cost.
---

# voicegw prices

Inspect and reconcile the billing rate card that turns recorded provider cost into a billable price.

## Synopsis

`voicegw prices` is the command group for VoiceGateway's rating layer. The rate card in `voicegw.yaml` maps recorded cost to a billable price and stamps that price immutably onto every request row (`rated_price_usd` + `rate_rule`). These subcommands read that card: `ls` prints it, `reconcile` rolls up rated revenue against recorded cost per tenant, and `sync` checks fixed-price rules against the current base cost. The full model (cost-plus vs fixed, tenant->plan->global resolution, write-time immutability) lives at [Rating](/architecture/rating).

## Usage

```bash
voicegw prices ls [-c PATH]
voicegw prices reconcile [-c PATH] [--period today|week|month] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--threshold FLOAT]
voicegw prices sync [-c PATH] [--threshold FLOAT]
```

## `voicegw prices ls`

Print the rate card in effect: the default markup followed by a table of rules. Each row shows the rule's scope (`provider/model (modality)`), tenant, plan, kind (`cost_plus` or `fixed`), and the audit token that will be stamped onto matching requests (for example `cost_plus:1.3` or `fixed:0.006/minute`). When the card has no rules, every request bills at the default markup and the table is skipped.

### Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |

### Example

```bash
voicegw prices ls
voicegw prices ls -c ./voicegw.yaml
```

## `voicegw prices reconcile`

Roll up rated revenue against recorded cost per tenant over a window, then flag tenants whose margin is thin (under the `--threshold` percent of rated revenue) or negative. This is the margin health check for a billing cycle: a tenant that is losing money or barely breaking even becomes visible before the invoice goes out.

### Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--period` | | `string` | `month` | Window to roll up: `today`, `week`, or `month`. |
| `--start` | | `string` | `null` | Start date in `YYYY-MM-DD`. Overrides `--period` when combined with `--end`. |
| `--end` | | `string` | `null` | End date in `YYYY-MM-DD` (inclusive day). |
| `--threshold` | | `float` | `20.0` | Flag tenants keeping under this percent of rated revenue as margin. |

### Prerequisites

- Cost tracking enabled in `voicegw.yaml` (the SQLite backend must be configured, or the command exits with 1).

### Output

A table with one row per tenant: tenant, requests, cost, rated, margin, margin %, and a flag. The flag is `ok`, `thin` (yellow), or `NEGATIVE` (red). When the window has no billable usage, the command prints `No billable usage in the window.` instead of a table.

### Example

```bash
# Current month, default 20% thin threshold
voicegw prices reconcile

# Explicit window, stricter threshold
voicegw prices reconcile --start 2026-06-01 --end 2026-06-30 --threshold 30
```

## `voicegw prices sync`

Check each fixed ($/unit) rule against the current voice-prices base cost for one unit, and flag rules whose margin is thin (under `--threshold`) or negative. A fixed rule advertises a flat `$/unit` that is decoupled from the base cost, so when the base moves the rule can silently cross into a loss. `cost_plus` rules auto-follow the base and need no sync, so they are not listed.

Per-unit base cost is resolvable only for concrete-model STT (`minute` / `second`) and TTS (`char` / `1k_char`) rules. Token and request blends (and wildcard-model rules) report `unresolvable` because there is no single per-unit base to compare against.

### Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--threshold` | | `float` | `20.0` | Flag fixed rules keeping under this percent margin as thin. |

### Output

A table with one row per fixed rule: scope, unit, fixed price, base cost, margin, margin %, and a flag (`ok`, `thin`, `NEGATIVE`, or `unresolvable`). When the card has no fixed rules, the command prints `No fixed rules to sync; cost-plus rules auto-follow the base price.` instead of a table.

### Example

```bash
voicegw prices sync
voicegw prices sync --threshold 25
```

## Examples

### Print the card, then check margins for last month

```bash
voicegw prices ls
voicegw prices reconcile --period month
```

### Verify fixed rules still hold their margin after a pricing refresh

```bash
voicegw prices sync --threshold 20
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Config could not be loaded, or (for `reconcile`) cost tracking is not enabled. |

<Note>
Editing the rate card at runtime (`voicegw prices set`) is a planned follow-up, tracked with the DB override store. Today the card is the YAML seed in `voicegw.yaml`; edit the file and reload the gateway to change rates.
</Note>

## Related

[`voicegw reconcile`](/cli/reconcile) | [`voicegw costs`](/cli/costs)

## See also

- [Rating](/architecture/rating): the rating layer end-to-end (cost-plus vs fixed, resolution, write-time immutability).
- [voicegw.yaml reference](/configuration/voicegw-yaml): the `rate_card:` config block these commands read.
- [HTTP API Reference](/api/http-api): the `GET /v1/billing/usage` and `GET /v1/billing/rate-card` endpoints fed by the same rating layer.
