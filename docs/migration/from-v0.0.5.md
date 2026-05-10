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

v0.0.5's `voicegw status` printed a single Provider Status table.
v0.1.0 prints the daemon view FIRST, then the provider view:

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

This is design.md decision 4 for the v0.1.0 release: the most
common reason `voicegw status` is consulted in v0.1.0 is "is my
gateway running?", and that is now the first row.

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
voicegw status        # daemon-first per the section above
```

Each delegates to a per-OS backend (LaunchAgent on macOS,
`systemd --user` on Linux, Scheduled Task on Windows; WSL uses
the Linux backend transparently).

### `voicegw doctor` {#doctor}

Ten checks, plain-language fix steps, no stack traces. Run it
whenever something is off; the output explicitly tells you what
to do for each failed check.

### `voicegw migrate` {#migrate}

Detects a v0.0.5 install (config + SQLite at
`~/.config/voicegateway/`), copies into the v0.1.0 layout
(same path; v0.0.5's config home is preserved per design
decision 2), and re-encrypts any managed provider keys with the
current Fernet key.

The migration writes everything to a staging path and
atomic-renames at the end. Failure leaves the v0.0.5 install
untouched.

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
