# Replay storage costs

v0.3.0's conversation replay captures every STT chunk, LLM token, TTS frame, and conversation-state snapshot for every session. This page surfaces the on-disk storage cost so the trade-off between fidelity and footprint is visible before you set per-project retention.

## What gets stored

Four tables per the [migration 0004 schema](https://github.com/mahimailabs/voicegateway/blob/main/voicegateway/storage/migrations/0004_replay_tables.py):

| Table | Row payload | Typical row size |
|---|---|---|
| `replay_stt_events` | JSON: `text`, `is_final`, `alternatives` | 100 - 400 bytes |
| `replay_llm_tokens` | JSON: `token_text`, `role`, `is_tool_invoke`, `tool_args_partial` | 80 - 200 bytes |
| `replay_tts_frames` | JSON: `frame_duration_ms`, `underrun`, `voice_id` | 80 - 120 bytes |
| `replay_state_snapshots` | JSON: `system_prompt`, `message_history`, `tool_call_in_flight`, `structured_output_collected` | 500 - 5000 bytes (depends on prompt + history size) |

Every row also carries the boilerplate: `id`, `session_id`, `t_ms`, `provider`, `cost_usd`, `created_at`. Add roughly 80-120 bytes of column overhead per row. Indexes on `(session_id, t_ms)` add another 30-50% of payload size.

## Per-minute estimate

For a typical voice conversation (caller speaks for half the time, agent for half, normal-cadence LLM with a 500-token system prompt):

| Modality | Events per minute | Bytes per minute |
|---|---|---|
| STT (partials + finals) | 30 - 60 | 8 KB - 24 KB |
| LLM tokens | 200 - 500 | 30 KB - 80 KB |
| TTS frames (20-50ms each) | 600 - 1500 | 60 KB - 180 KB |
| State snapshots (1/sec cap) | 60 | 30 KB - 300 KB |

**Total: roughly 130 KB - 580 KB per minute of conversation**, with the floor for short crisp exchanges and the ceiling for chatty agents with long conversation histories. The Foundry's design target of 30-100 KB/min is achievable on the floor; realistic agents will land closer to the ceiling.

The Foundry's [Open Question 1 step-zero smoke test](https://github.com/mahimailabs/voicegateway/blob/main/.agents/journal.md) (T18 of v0.3.0) measures the actual figure on a synthetic conversation; if the smoke shows you trending above 500 KB/min consistently, the per-project `replay.enabled: false` toggle is the fastest mitigation.

## Worked example

A solo developer running 100 voice calls per day, averaging 5 minutes each:

```
100 calls/day × 5 min/call × 200 KB/min = 100,000 KB/day ≈ 100 MB/day
```

At the default 90-day retention:

```
100 MB/day × 90 days ≈ 9 GB total replay storage
```

At AWS S3 standard storage prices (~$0.023/GB-month):

```
9 GB × $0.023 ≈ $0.21/month
```

On the local SQLite database (no cloud markup), the cost is the disk byte cost: ~$0.01/GB-month on a developer SSD ≈ ~$0.09/month for the same 9 GB.

A team agency running 10,000 conversations per day at 3 minutes average scales linearly: ~3 TB at 90-day retention, ~$70/month on S3 standard or ~$30/month on local disk. At that point the `retention_days` knob matters: dropping to 30 days cuts storage to one-third.

## Tuning knobs

Three per-project knobs in `voicegw.yaml`'s `replay:` block influence storage:

| Knob | Default | Effect |
|---|---|---|
| `enabled` | `true` | Capture for every session. Set `false` to skip capture entirely. |
| `retention_days` | `90` | Age replay rows out after this window. Lower to reduce footprint linearly. |
| `flush_size_events` | `500` | Batched writes; smaller writes more often, larger holds more memory. No storage effect. |
| `buffer_size_events` | `5000` | In-memory cap. Above this, oldest events are dropped with a counter. The dashboard surfaces dropped events as "events dropped here" rather than silently misleading the developer. |

The `enabled` toggle is the binary on/off. The `retention_days` knob is the gradient lever. The `buffer_size_events` and `flush_size_events` knobs trade off memory pressure and write batching but do not change long-term storage volume.

## Dashboard storage view

`GET /api/replay/storage` returns per-project replay byte totals (T10 of v0.3.0). The dashboard surfaces this as a breakdown so the developer sees the cost in real time:

```json
{
  "total_replay_size_bytes": 9234567890,
  "by_project": [
    {"project": "acme", "replay_size_bytes": 8000000000},
    {"project": "default", "replay_size_bytes": 1234567890}
  ]
}
```

A v0.3.x follow-up will surface this in a sidebar panel on the dashboard with cost-per-month estimates inline.

## Related

- [Python SDK reference > Conversation replay capture](/api/python-sdk#conversation-replay-capture-v030)
- [Migration 0004 schema](https://github.com/mahimailabs/voicegateway/blob/main/voicegateway/storage/migrations/0004_replay_tables.py)
- [`voicegw replay <session-id>` CLI signpost](/cli/replay) (T15 of v0.3.0)
