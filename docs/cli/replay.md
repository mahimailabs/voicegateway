---
title: Session replay
description: "Capture conversation-state snapshots with attach(snapshots=True) and step through them on the dashboard's Replay page."
---

Replay reconstructs what the agent was thinking at each turn: the system prompt, the
message history as it grew, and every tool call with its arguments and result. It is
opt-in, and nothing is captured until you ask for it.

## Capture

```python
voicegateway.attach(session, project="my-agent", snapshots=True)
```

A snapshot is written at each completed message, rate-capped to one per second, and at
each resolved tool call, which bypasses the cap because tool calls are rare and are the
most useful thing in a replay.

| Condition | Effect |
|---|---|
| `snapshots=True` | Capture on for this session |
| default (omitted) | Off |
| `VOICEGW_SNAPSHOTS=0` | Off fleet-wide, beats the argument |
| `VOICEGW_COLLECTOR_URL` set | Skipped: a collector has no replay tables |
| Pipecat | Flag accepted, no capture yet. LiveKit only |

<Warning>
Off by default on purpose. A snapshot is a larger disclosure than a transcript: it
carries your system prompt and whatever payloads your tools handle, not just what the
caller said. Turn it on deliberately, and see
[replay storage costs](/storage/replay-storage-costs) before leaving it on across a fleet.
</Warning>

## What is not captured

STT chunks, LLM tokens, and TTS frames. Those are per-chunk, per-token, and per-frame,
and capturing them would mean sitting inside the media and inference streams.
VoiceGateway does not do that, which is the same reason it adds no latency on happy-path
calls.

## View a replay

`voicegw replay` prints the dashboard URL for a session and exits. It renders nothing in
the terminal.

```bash
voicegw replay vg-123
voicegw replay vg-123 --dashboard-url https://voicegateway.example.com
```

| Flag | Default | Description |
|---|---|---|
| `--dashboard-url` | `http://127.0.0.1:8080` | Base URL of the daemon serving the dashboard |

<Note>
The daemon must already be running before the printed URL resolves. Start it with
`voicegw serve`. `voicegw dashboard` only opens a browser; it starts nothing.
</Note>

## Related

- [`attach()`](/guide/attach): the full signature, including `transcript` and `snapshots`.
- [Turns and transcripts](/guide/turns): what the caller and agent said, captured by default.
- [Replay storage costs](/storage/replay-storage-costs): retention and disk footprint.
