---
title: Replay storage costs
description: On-disk footprint of VoiceGateway's conversation replay tables. Understand the per-minute byte budget across STT, LLM, TTS, and state snapshot events before setting per-project retention.
---
VoiceGateway's conversation replay captures every STT chunk, LLM token, TTS frame, and conversation-state snapshot for a session, when capture is turned on. See [CLI: replay](/cli/replay) for how to enable capture (`attach(snapshots=True)`, LiveKit only, default off) and inspect a captured session. This page surfaces the on-disk storage cost so the trade-off between fidelity and footprint is visible before you set per-project retention.

## What gets stored

Four tables (`src/voicegateway/models/replay_event_model.py`), one row per captured event. Each table serializes its event fields into a single `payload` TEXT column rather than separate typed columns:

| Table | `payload` JSON keys | Typical payload size |
|---|---|---|
| `replay_stt_events` | `text`, `is_final`, `alternatives` | 100-400 bytes |
| `replay_llm_tokens` | `token_text`, `role`, `is_tool_invoke`, `tool_args_partial` | 80-200 bytes |
| `replay_tts_frames` | `frame_duration_ms`, `underrun`, `voice_id` | 80-120 bytes |
| `replay_state_snapshots` | `system_prompt`, `message_history`, `tool_call_in_flight`, `structured_output_collected` | 500-5000 bytes (depends on prompt and history size) |

`replay_stt_events`, `replay_llm_tokens`, and `replay_tts_frames` also carry `id`, `session_id`, `t_ms`, `provider`, `cost_usd`, `created_at`, `tenant_id`. `replay_state_snapshots` is narrower: no `provider` or `cost_usd` column. Column overhead runs roughly 80-120 bytes/row; the index on `(session_id, t_ms)` that each table carries adds an estimated 30-50% on top of payload size.

## Per-minute estimate

For a typical voice conversation (caller speaks for half the time, agent for half, normal-cadence LLM with a 500-token system prompt):

| Modality | Events per minute | Bytes per minute |
|---|---|---|
| STT (partials + finals) | 30-60 | 8 KB-24 KB |
| LLM tokens | 200-500 | 30 KB-80 KB |
| TTS frames (20-50ms each) | 600-1500 | 60 KB-180 KB |
| State snapshots (1/sec cap) | 60 | 30 KB-300 KB |

**Total: roughly 130 KB-580 KB per minute of conversation.** The floor applies to short, crisp exchanges; the ceiling applies to chatty agents with long conversation histories. The design target of 30-100 KB/min is achievable at the floor; realistic agents will land closer to the ceiling.

If you find yourself trending above 500 KB/min consistently, the per-project `replay.enabled: false` toggle is the fastest mitigation.

## Worked example

A solo developer running 100 voice calls per day, averaging 5 minutes each:

```
100 calls/day x 5 min/call x 200 KB/min = 100,000 KB/day ~ 100 MB/day
```

At the default 90-day retention:

```
100 MB/day x 90 days ~ 9 GB total replay storage
```

At AWS S3 standard storage prices (~$0.023/GB-month):

```
9 GB x $0.023 ~ $0.21/month
```

On the local SQLite database (no cloud markup), the cost is the disk byte cost: ~$0.01/GB-month on a developer SSD, so ~$0.09/month for the same 9 GB.

A team agency running 10,000 conversations per day at 3 minutes average scales linearly: ~3 TB at 90-day retention, ~$70/month on S3 standard or ~$30/month on local disk. At that point the `retention_days` knob matters: dropping to 30 days cuts storage to one-third.

## Tuning knobs

Three per-project knobs in `voicegw.yaml`'s `replay:` block influence storage:

| Knob | Default | Effect |
|---|---|---|
| `enabled` | `true` | Set `false` to skip capture entirely for this project. |
| `retention_days` | `90` | Age replay rows out after this window. Lower to reduce footprint linearly. |
| `flush_size_events` | `500` | Batched writes. Smaller flushes more often; larger holds more memory. No effect on long-term storage volume. |
| `buffer_size_events` | `5000` | Per-session in-memory cap. Above this limit, the oldest buffered events are dropped and a warning is logged server-side every 100 drops. Nothing in the dashboard currently surfaces the drop count. |

The `enabled` toggle is the binary on/off. The `retention_days` knob is the gradient lever. The `buffer_size_events` and `flush_size_events` knobs trade off memory pressure and write batching but do not change long-term storage volume.

## Dashboard storage view

`GET /api/replay/storage` returns per-project replay byte totals. The dashboard surfaces this as a breakdown so you see the cost in real time:

```json
{
  "total_replay_size_bytes": 9234567890,
  "by_project": [
    {"project": "acme", "replay_size_bytes": 8000000000},
    {"project": "default", "replay_size_bytes": 1234567890}
  ]
}
```

## Related pages

<CardGroup cols={2}>
  <Card title="Storage" href="/architecture/storage">
    The full SQLite schema including the replay tables.
  </Card>
  <Card title="CLI: replay" href="/cli/replay">
    Inspect a specific session with voicegw replay.
  </Card>
  <Card title="Cost reconciliation" href="/guide/cost-reconciliation">
    Verify recorded costs against provider invoices.
  </Card>
  <Card title="Architecture index" href="/architecture/index">
    Overview of all architecture pages.
  </Card>
</CardGroup>
