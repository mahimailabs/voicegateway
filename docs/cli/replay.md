---
title: voicegw replay
description: Print the dashboard Replay page URL for a recorded session.
---
Print the dashboard Replay page URL for a recorded session.

## Synopsis

`voicegw replay` is a signpost to the graphical dashboard timeline. It outputs the URL for the given session and exits. It does not render replay events in the terminal.

## Usage

```bash
voicegw replay <session-id> [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--dashboard-url` | | `string` | `http://127.0.0.1:8080` | Base URL of the dashboard (the daemon's serve port). |

## Examples

### Open a session in the dashboard

```bash
voicegw replay vg-123
```

### Point at a remote dashboard

```bash
voicegw replay vg-123 --dashboard-url https://voicegateway.example.com
```

<Note>
The dashboard must already be running before following the printed URL. Start it with `voicegw dashboard` or confirm the daemon is up with `voicegw status`.
</Note>

## Related

[`voicegw dashboard`](/cli/dashboard) | [`voicegw status`](/cli/status) | [`voicegw logs`](/cli/logs)

## See also

- [Replay storage costs](/storage/replay-storage-costs): retention and storage trade-offs.
