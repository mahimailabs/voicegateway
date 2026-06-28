---
title: voicegw tui
description: Launch the four-tab terminal UI for live monitoring of sessions, costs, logs, and providers.
---

# voicegw tui

Launch the four-tab terminal UI for live monitoring of sessions, costs, logs, and providers.

## Purpose

The `tui` command opens a Textual-based terminal interface that mirrors the web dashboard's read paths in a vim-friendly layout. Use it during local development for at-a-glance feedback on conversations and costs, during incident response when the daemon is partially degraded, or for postmortem inspection of the SQLite call DB after the daemon has been stopped.

Two modes are supported:

- **Gateway mode** (default): polls the running daemon at 1 s cadence. Read paths and the Providers `t` test shortcut are available.
- **Local mode** (`--local`): reads SQLite directly at 5 s cadence. Read paths only; write attempts raise `LocalModeUnsupportedError` and surface as a visible warning. A persistent `[Local mode]` chip is rendered in the header.

## Syntax

```bash
voicegw tui [OPTIONS]
```

## Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--local` | flag | off | Read SQLite directly; bypass the daemon regardless of its state. |
| `--url` | `string` | from `voicegw.yaml` | Daemon URL. `0.0.0.0` from `serve.host` is rewritten to `127.0.0.1`. |
| `--token` | `string` | `null` | Bearer token for daemon write paths. Required for the Providers `t` shortcut. |
| `--history-limit` | `integer` | `100` | Initial row count for the Sessions and Logs tabs. |
| `--theme` | `string` | `brand` | Color theme. `brand` is the default. |
| `--poll` | `float` | mode-dependent | Polling cadence in seconds. Defaults: `1.0` (Gateway), `5.0` (Local). |
| `--config` / `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |

## Prerequisites

The `tui` extra must be installed:

```bash
pip install "voicegateway[tui]"
```

`pipx` users on a system install can inject it without reinstalling:

```bash
pipx inject voicegateway "voicegateway[tui]"
```

For Gateway mode, the daemon must be reachable at the resolved URL. A 2 s preflight probe hits `GET /health` before Textual takes over the terminal; an unreachable daemon prints a one-line pointer and exits with code 1.

## Tabs

Each tab is reachable by its number key. Vim-style `h/j/k/l` and `g/G` work for movement; `?` opens a help overlay listing every binding active on the current screen.

### Sessions (`1`)

Recent voice conversations sorted by start time or total cost. Each row shows start time, duration, total cost, and the providers used. Press `s` to toggle the sort column; press `Enter` to open a per-turn drill-in modal for the focused row.

### Costs (`2`)

Today's total as a single dollar number, with a per-modality breakdown (STT, LLM, TTS) beneath. Press `r` to cycle the active range between today, this week, and this month. Pricing is sourced from `voice-prices`, which version-stamps each source (`voice-prices@<version>`).

### Logs (`3`)

Recent request logs in a `RichLog`-based tail viewer. New lines appear at the bottom in real time (polling-based; SSE is a future enhancement). Press `/` to filter to a substring; press `Escape` to clear the filter. Scrolling up disables auto-scroll until you return to the bottom.

### Providers (`4`)

Configured providers with a one-character indicator: `[ok]`, `[fail]`, or `[?]`. The owning project appears next to each provider; global providers show `(global)`. Press `t` on a focused row to run `test_provider` against the upstream API; the indicator flips within 3 s.

In Local mode, `t` raises `LocalModeUnsupportedError(feature='test_provider')`; the row's status stays unchanged and a warning notification (title: `Action requires the daemon`) surfaces so the failure is visible, not silent.

## Vim Keybindings

Vim navigation is consistent across all four tabs.

### Global (always active)

| Key | Action |
|---|---|
| `q` | Quit |
| `?` | Toggle help overlay |
| `1` / `2` / `3` / `4` | Jump to Sessions / Costs / Logs / Providers |
| `Tab` / `Shift+Tab` | Cycle tabs forward / backward |

### Row-based screens (Sessions, Providers)

| Key | Action |
|---|---|
| `j` / `k` | Focus next / previous row |
| `h` / `l` | Alias for `k` / `j` (hidden in the footer hint) |
| `g` / `G` | Focus first / last row |

### Sessions extras

| Key | Action |
|---|---|
| `s` | Toggle sort column (cost ↔ start time) |
| `Enter` | Open per-turn detail modal |
| `Escape` / `q` | Close detail modal |

### Costs extras

| Key | Action |
|---|---|
| `r` | Cycle range (today → this week → this month) |

### Logs extras

| Key | Action |
|---|---|
| `j` / `k` | Scroll down / up one line |
| `g` / `G` | Scroll to top / bottom |
| `/` | Open filter input |
| `Escape` | Clear filter |

### Providers extras

| Key | Action |
|---|---|
| `t` | Run `test_provider` against the focused row |

## Mode Comparison

| Aspect | Gateway mode | Local mode (`--local`) |
|---|---|---|
| Data source | Daemon HTTP API at `--url` | SQLite call DB directly |
| Default poll cadence | 1.0 s | 5.0 s |
| Header chip | none | `[Local mode]` on warning background |
| Footer staleness suffix | live, no suffix | `(as of X ago)` from SQLite mtime |
| Write actions (`t` shortcut) | live test against upstream | surfaces `LocalModeUnsupportedError` as warning notification |
| Daemon required | yes (preflight enforced) | no (works when the daemon is down) |
| Typical use | daily monitoring during development | postmortem after a daemon crash, audit on a stranger's machine |

## Examples

### Launch against the running daemon

```bash
voicegw tui
```

Resolves the daemon URL from `voicegw.yaml`, runs a 2 s health preflight, and opens the Sessions tab.

### Launch in Local mode

```bash
voicegw tui --local
```

Bypasses the daemon and reads the SQLite call DB directly. The `[Local mode]` chip stays visible in the header for the entire session.

### Connect to a remote daemon

```bash
voicegw tui --url https://gateway.internal:8080 --token "$VG_TOKEN"
```

Bearer token is sent on the health preflight and on every API call, including the Providers `t` shortcut.

### Slower polling for shared environments

```bash
voicegw tui --poll 5.0
```

Useful when running against a daemon that is also serving production traffic and you do not want the TUI's polling to register as a noticeable load.

## Troubleshooting

### Terminal too small

The TUI assumes at least 80 cols × 24 rows. On smaller terminals, row content truncates and the help overlay may not render fully. Resize the terminal or open it in a fresh window.

### Colors look wrong on 256-color terminals

Textual's `Color.downgrade()` auto-maps the brand-orange `#D85A30` to xterm color 166 on 256-color terminals and to color 3 (yellow) or 1 (red) on 16-color terminals. If colors look off, verify `TERM` is `xterm-256color` (or similar). No code change is needed.

### Local mode write attempts

The `t` shortcut on Providers raises `LocalModeUnsupportedError` in Local mode. This is by design: the daemon is the only path that holds the upstream provider sessions and rate-limit budget. The warning notification names the unsupported feature (`test_provider`). To run a real test, start the daemon (`voicegw start`) and relaunch the TUI without `--local`.

### Daemon unreachable

If `voicegw tui` exits with `Daemon at <url> is not reachable`, the 2 s health preflight failed. Verify the daemon is running (`voicegw status`) and that the `--url` is correct. For read-only inspection of the SQLite call DB without the daemon, use `voicegw tui --local`.

### Reconnection

If the daemon stops responding mid-session, the footer flips to `Reconnecting to daemon...`. The next successful poll cycle replaces it with the live counter. The polling cadence (1 s default in Gateway mode) is the retry interval; exponential backoff is deferred to a future milestone.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Clean exit (user pressed `q` or `Ctrl+C`). |
| `1` | Daemon unreachable in Gateway mode, or any launch-time failure (corrupt SQLite in Local mode, missing `[tui]` extra, etc.). |

## Related Commands

- [`voicegw status`](/cli/status) -- quick non-interactive provider status
- [`voicegw dashboard`](/cli/dashboard) -- the web counterpart to the TUI
- [`voicegw costs`](/cli/costs) -- non-interactive cost summary
- [`voicegw logs`](/cli/logs) -- non-interactive log tail
