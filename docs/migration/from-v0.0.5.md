---
title: Migrating from VoiceGateway v0.0.5 to v0.1.0
description: What changes between v0.0.5 (LiveKit Cloud parity) and v0.1.0 (daemon-first onboarding), and what to do about it.
---

# Migrating from v0.0.5 to v0.1.0

v0.1.0 is additive: every v0.0.5 import path, the inference module,
the dashboard frontend, and the storage schema all keep working
unchanged. What's new is the operational substrate that turns a
fresh-machine install into a working gateway in 60 seconds:

- [`voicegw onboard`](#onboard) drives a five-question wizard.
- [`voicegw start` / `stop` / `restart`](#lifecycle) wrap the
  per-OS service manager.
- [`voicegw doctor`](#doctor) prints a numbered punch list when
  something is off.
- [`voicegw migrate`](#migrate) brings a v0.0.5 install into the
  v0.1.0 layout in place.

Run `voicegw migrate` once after upgrading; it is idempotent and
preserves the v0.0.5 install if anything fails partway through.

## What changed in `voicegw status` {#status}

v0.0.5 printed a single Provider Status table. v0.1.0 prints the
daemon view FIRST, then the provider view (design decision 4: the
most common reason `voicegw status` is consulted is "is my gateway
running?", and that is now the first row).

**Before (v0.0.5):**

```text
       Provider Status
 ┃ Provider ┃ Configured ┃ Models ┃
 ┃ openai   ┃ Yes        ┃ 4      ┃
 ┃ deepgram ┃ Yes        ┃ 1      ┃
```

**After (v0.1.0):**

```text
                Daemon
 Registered  yes
 Running     yes
 PID         12345

       Provider Status
 ┃ Provider ┃ Configured ┃ Models ┃
 ┃ openai   ┃ Yes        ┃ 4      ┃
 ┃ deepgram ┃ Yes        ┃ 1      ┃
```

If the daemon backend fails to load (e.g., when running inside a
sandbox without the matching OS service tools), the daemon section
prints a yellow `Daemon status unavailable: ...` line and the
provider section renders unchanged.

When the daemon is `Registered: no`, the status output points at
`voicegw onboard --install-daemon` to fix it.

## What's new in v0.1.0

### `voicegw onboard` {#onboard}

Five questions:

1. Project name (default: `default`).
2. Provider (default: `openai`).
3. API key (validated against the provider with a 5-second
   timeout; soft-warn on timeout).
4. Port (default: `8080`).
5. Install daemon? (default: yes).

At the end, the wizard offers a smoke-test that ends in a
dashboard row.

### Lifecycle commands {#lifecycle}

```bash
voicegw start
voicegw stop
voicegw restart
voicegw status              # daemon-first per the section above
voicegw daemon-logs --tail 50   # tail the OS-native daemon log stream
voicegw uninstall-daemon    # remove registration; preserves config + DB
```

Each delegates to a per-OS backend (LaunchAgent on macOS,
`systemd --user` on Linux, Scheduled Task on Windows; WSL uses
the Linux backend transparently). `daemon-logs` routes to the
right OS surface (`log show` on macOS, `journalctl --user-unit
voicegateway` on Linux, the per-user log file under
`%LOCALAPPDATA%` on Windows) so you don't need to remember which
tool each platform uses. `uninstall-daemon` removes the
OS-level registration only and prints exactly what was preserved
(config file, call DB, encrypted managed_providers rows) plus the
documented manual cleanup command (`rm -rf ~/.config/voicegateway/`).

### `voicegw doctor` {#doctor}

Ten checks rendered as a numbered Rich punch list. Every failed
check carries a specific fix action: no stack traces, no bare
"see docs" pointers.

1. Python version (3.11+)
2. pipx installed
3. Daemon registered with the OS service manager
4. Daemon running
5. Port conflict on the configured serve port
6. Provider configured in voicegw.yaml
7. Provider key validates against the upstream API (5-second cap, fail-soft)
8. Recent error count low (storage scan)
9. Dashboard reachable on its bind port
10. MCP responsive (best-effort; stdio MCP has no probe surface)

Three statuses appear: `PASS` (green), `FAIL` (red, drives exit
code 1), and `SKIP` (yellow, the documented non-blocking status
for "this check doesn't apply right now"). Run `voicegw doctor`
whenever something is off; the output explicitly tells you what
to do for each failed check.

### `voicegw migrate` {#migrate}

Detects a v0.0.5 install (config + SQLite at
`~/.config/voicegateway/`) and verifies the integrity of the
existing files. Per design decision 2 the v0.1.0 path matches
v0.0.5, so there is no copy step; the command is read-only.

What `voicegw migrate` checks:

- `voicegw.yaml` parses cleanly under the v0.1.0 schema.
- `voicegw.db` opens and has the expected tables.
- Managed provider keys decrypt under the current
  `VOICEGW_SECRET` (if any are missing or under-encrypted, the
  command points at `voicegw rotate-secret`).
- Daemon registration status (so the next-step pointer surfaces
  `voicegw onboard --install-daemon` when needed).

The output ends with an explicit "this command is read-only;
no files were written; your v0.0.5 install is unchanged"
footer. Idempotent on re-run (byte-identical output across
back-to-back invocations). The atomic-write seam
(`_atomic_write_text`) ships ready for the first schema bump
that introduces a write; today there is nothing to roll back
from.

## What did NOT change

- Every v0.0.5 import keeps working: `from voicegateway import
  inference`, `from voicegateway.cli import app`, `Gateway`,
  `ModelId`, `GatewayConfig`.
- The inference module's drop-in shape against
  `livekit.agents.inference` is identical.
- The dashboard frontend is unchanged.
- Storage schema is unchanged. Existing call history survives
  migration unchanged.
- The `voicegw smoke-test`, `voicegw costs`, `voicegw export-costs`,
  `voicegw reconcile`, and `voicegw mcp` commands all behave
  exactly as in v0.0.5.

## What's now deferred

- The metrics-dashboard view (originally v0.0.6, now v0.2.0)
  is paused until v0.1.0 adoption proves the operational
  hypothesis. Nothing changes for users today; it just
  doesn't ship in v0.1.0.
