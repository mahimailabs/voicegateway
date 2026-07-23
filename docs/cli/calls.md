---
title: voicegw calls
description: List recent calls with per-call cost and activity, the unit an operator thinks in.
---

# voicegw calls

List recent calls with per-call cost and activity.

## Synopsis

`voicegw calls` shows one row per call (a session), not per model request. Where [`voicegw costs`](/cli/costs) rolls spend up by provider and model, and [`voicegw logs`](/cli/logs) tails individual STT/LLM/TTS requests, `calls` answers the operator's question: what did each call cost, and how busy was it?

## Usage

```bash
voicegw calls [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--project` | `-p` | `string` | `null` | Filter to a specific project ID. |
| `--limit` | `-n` | `int` | `20` | How many calls to show. |
| `--sort` | `-s` | `string` | `recent` | Order: `recent` (newest first) or `cost` (most expensive first). |

## Prerequisites

Cost tracking must be enabled in `voicegw.yaml`:

```yaml
cost_tracking:
  enabled: true
```

If cost tracking is disabled, the command prints a warning and exits.

## Output

A **Recent Calls** table with, per call: the call (session) id, when it started, the modalities used, the request count, and the total cost. A footer summarizes the shown calls (count, total cost, average cost per call).

## Examples

### Recent calls

```bash
voicegw calls
```

```
             Recent Calls (20)
┌──────────┬─────────────┬─────────────┬──────────┬─────────┐
│ Call     │ Started     │ Modalities  │ Requests │    Cost │
├──────────┼─────────────┼─────────────┼──────────┼─────────┤
│ call-009 │ 07-23 03:52 │ stt·llm·tts │       15 │ $0.0513 │
│ call-017 │ 07-21 20:59 │ stt·llm·tts │       18 │ $0.1055 │
└──────────┴─────────────┴─────────────┴──────────┴─────────┘

20 calls · $1.1942 total · $0.0543 avg/call
```

### The most expensive calls

```bash
voicegw calls --sort cost -n 10
```

### One project

```bash
voicegw calls --project tonys-pizza
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success (including when cost tracking is disabled; prints warning). |
| `1` | Config failed to load. |
| `2` | Unknown `--sort` value. |

## Related

[`voicegw costs`](/cli/costs) | [`voicegw logs`](/cli/logs) | [`voicegw reconcile`](/cli/reconcile)
