# voicegw replay

Print the dashboard Replay page URL for a recorded session.

```bash
voicegw replay <session-id>
```

The command is a signpost to the graphical dashboard timeline. It does not render replay events in the terminal.

## Options

| Option | Description |
|---|---|
| `--dashboard-url` | Base URL of the dashboard. Defaults to `http://127.0.0.1:8080` (the daemon's serve port). |

## Examples

```bash
voicegw replay vg-123
voicegw replay vg-123 --dashboard-url https://voicegateway.example.com
```

The dashboard must already be running:

```bash
voicegw dashboard
```

See [Replay storage costs](/storage/replay-storage-costs) for retention and storage trade-offs.
