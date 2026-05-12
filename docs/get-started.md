---
title: Get started
description: Install VoiceGateway, run the onboarding wizard, see your first provider call land in the dashboard. 60 seconds, excluding the time it takes to fetch your provider API key.
---

# Get started

VoiceGateway runs as a long-lived background daemon. From a fresh
machine to your first inference call: one curl, one wizard, one
status command. The 60-second clock starts when you paste the
install command and stops when your first call shows up in the
dashboard, excluding the time spent fetching your provider API key.

## Feel it first

<DemoWidget placement="docs" />

## 1. Install

```bash
curl -fsSL https://voicegateway.mahimai.ca/install.sh | bash
```

What the installer does:

- Detects macOS, Linux, or WSL on Windows. Refuses cleanly on
  anything else.
- Verifies Python 3.11 or newer is installed. Refuses with package
  manager pointers if not (does not auto-install Python).
- Installs pipx if missing. Asks before any privileged step.
- Runs `pipx install voicegateway[cloud,dashboard]`.
- Prints the next-step command.

If you prefer not to pipe a remote script:

```bash
pipx install voicegateway[cloud,dashboard]
```

::: tip Two domains is on purpose
install.sh is hosted at `voicegateway.mahimai.ca`. This docs site
lives at `vg.openrtc.tech`. Both are operated by the VoiceGateway
team. The split is deliberate (separate static-host providers); it
is not a phishing redirect.
:::

## 2. Run the wizard

```bash
voicegw onboard --install-daemon
```

Five questions, four with working defaults (press Enter to accept):

1. **Project name** (default: `default`).
2. **Provider** (default: `openai`).
3. **API key** (no default; paste yours).
4. **Port** (default: `8080`).
5. **Install daemon?** (default: yes).

The wizard then:

- Validates your API key against the provider with a 5-second
  timeout. On timeout, the wizard continues with a warning instead
  of failing.
- Registers a user-scoped daemon (LaunchAgent on macOS,
  `systemd --user` unit on Linux, Scheduled Task on Windows).
- Runs `voicegw smoke-test` and waits for one request row to
  appear in the dashboard.
- Prints the dashboard URL and the next-step commands.

::: tip Cancel safely with Ctrl+C
Pressing Ctrl+C at any point cleans up partial state. Re-running
the wizard is always safe.
:::

## 3. See it work

```bash
voicegw status        # daemon + provider status
voicegw doctor        # ten-check punch list with fix steps
voicegw logs          # tail recent activity
```

Open the dashboard URL the wizard printed (default
`http://127.0.0.1:9090`) and watch your first call land.

From here, wire VoiceGateway into your LiveKit agent by swapping
one import line:

```python
from voicegateway import inference

stt = inference.STT("deepgram/nova-3")
llm = inference.LLM("openai/gpt-4.1-mini")
tts = inference.TTS("openai/tts-1")
```

Pass each to `AgentSession` exactly as you would the upstream
`livekit.agents.inference` equivalents. Cost tracking, latency
monitoring, and per-session correlation happen behind the scenes.
See [Quick Start](/guide/quick-start) for the full integration
walkthrough.

---

## Troubleshooting

The three most common things that block first-call:

### Python 3.11+ is missing

install.sh refuses on Python below 3.11 because the cloud provider
SDKs have already moved past 3.10. Install Python first, then
re-run install.sh:

::: code-group

```bash [macOS]
brew install python@3.12
```

```bash [Debian/Ubuntu]
sudo apt install python3.12 python3.12-venv
```

```bash [Fedora/RHEL]
sudo dnf install python3.12
```

:::

VoiceGateway will not auto-install Python. That choice is left to
your OS package manager so we never silently shadow a system
Python or break a virtualenv you cared about.

### pipx is missing or not on PATH

install.sh installs pipx automatically when it can. Two failure
modes are common:

1. pipx installed but `pipx` is not on your PATH yet. Open a fresh
   shell and try again, or add `~/.local/bin` to your PATH:

   ```bash
   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
   exec $SHELL
   ```

2. The installer printed `pipx ensurepath` and exited cleanly.
   Your installed-as-user directory is registered but the current
   shell did not pick it up yet. Open a new terminal tab and
   re-run `voicegw onboard --install-daemon`.

### Provider key is invalid

The wizard validates your key against the provider with a 5-second
timeout. If validation times out, the wizard prints a warning and
continues. If validation returns `401 unauthorized` or similar:

1. Double-check the key in your provider dashboard. The most
   common cause is a missing character, a stray newline, or the
   wrong project selected when you generated the key.
2. Re-run the wizard. It is idempotent: any partial state from a
   previous attempt is overwritten cleanly.
3. Run `voicegw doctor`. Check 7 (provider key validation) prints
   exactly which key was rejected and what to verify.

---

For anything else, `voicegw doctor` is the first place to look. It
prints a numbered punch list with concrete fix steps for each
failed check. No stack traces. No bare "see docs" pointers.
