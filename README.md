<div align="center">

<img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/site/docs/public/assets/cover.jpg" alt="VoiceGateway: see what every voice call costs" width="100%" />

<p>
  <a href="https://pypi.org/project/voicegateway"><img src="https://img.shields.io/pypi/v/voicegateway?color=cba6f7&labelColor=0a0a0a" alt="PyPI"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT%20core-cba6f7?labelColor=0a0a0a" alt="MIT core license"/></a>
  <a href="https://docs.voicegateway.dev"><img src="https://img.shields.io/badge/docs-voicegateway.dev-cba6f7?labelColor=0a0a0a" alt="Docs"/></a>
  <a href="https://discord.gg/ysFaF4uSB"><img src="https://img.shields.io/badge/Discord-join-cba6f7?labelColor=0a0a0a&logo=discord&logoColor=white" alt="Discord"/></a>
</p>

[**Docs**](https://docs.voicegateway.dev) · [**Live demo**](https://voicegateway.dev/demo) · [**Good first issues**](https://github.com/mahimailabs/voicegateway/issues?q=is%3Aopen+label%3A%22good+first+issue%22)

</div>

**The open-source profiler for voice agents.** Voice AI vendors hide three numbers: whether a call worked, what it cost, and how to make it cheaper. Add one line and VoiceGateway prices and times every STT, LLM and TTS call in your LiveKit or Pipecat agent. Self-hosted, on your own keys.

```python
from livekit.agents import AgentSession
from livekit.plugins import cartesia, deepgram, openai
import voicegateway

session = AgentSession(
    stt=deepgram.STT(model="nova-3"),
    llm=openai.LLM(model="gpt-4.1-mini"),
    tts=cartesia.TTS(model="sonic-3"),
)
voicegateway.attach(session)
```

Every call then lands as a row you can read, per stage and in total (example numbers):

```text
call 8f2c   STT  deepgram/nova-3       0.95 audio min      $0.00456
            LLM  openai/gpt-4.1-mini   1,320 in, 220 out   $0.00088
            TTS  cartesia/sonic-3      860 chars           $0.03440
            total $0.03984 · 640 ms to first audio
```

<details>
<summary><b>Using Pipecat?</b> Same one line.</summary>

```python
from pipecat.pipeline.task import PipelineTask
import voicegateway

task = PipelineTask(pipeline)
voicegateway.attach(task)
```

</details>

## Quick start

```bash
pip install "voicegateway[livekit,dashboard]"   # or [pipecat,livekit,dashboard]
voicegw init && voicegw serve                    # dashboard at http://localhost:8080
```

Add the `attach()` line to your agent and run one call. VoiceGateway bundles no provider plugins: it meters the ones you already use. The [quickstart](https://docs.voicegateway.dev/docs/quickstart) walks through a first call end to end. Python 3.11+.

## What it does

- **Prices every call** by audio seconds, tokens and characters, through [voice-prices](https://github.com/mahimailabs/voice-prices). [How attach works](https://docs.voicegateway.dev/docs/attach)
- **Checks itself against your invoices.** `voicegw reconcile` compares recorded cost with a provider's usage export. [Costs and reconciliation](https://docs.voicegateway.dev/docs/costs)
- **Controls spend.** `guard()` wraps one provider with a daily budget, fallback and rate limit, and returns the same type. [guard()](https://docs.voicegateway.dev/docs/guard)
- **Collects a fleet.** One collector on your own server, every agent pushes to it. [Self-hosting](https://docs.voicegateway.dev/docs/self-hosting)
- **Talks to coding agents.** An MCP server lets Claude Code, Cursor and Codex query costs and calls. [MCP](https://docs.voicegateway.dev/docs/self-hosting#coding-agents-mcp)

Building a text-only LLM app? [LiteLLM](https://docs.litellm.ai/) is the better fit. VoiceGateway is for agents that listen and speak.

## See it first

<a href="https://voicegateway.dev/demo"><img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/site/docs/public/assets/dashboard.png" alt="The VoiceGateway dashboard: cost by provider and model" width="100%" /></a>

Click through the real dashboard with example data, no install and no login, at [voicegateway.dev/demo](https://voicegateway.dev/demo).

## Contribute in ten minutes

1. Pick an issue labelled [good first issue](https://github.com/mahimailabs/voicegateway/issues?q=is%3Aopen+label%3A%22good+first+issue%22). Each one names the files, the change and the test.
2. Comment `.take` to claim it. First comment wins.
3. Set up and run the tests:

   ```bash
   git clone https://github.com/mahimailabs/voicegateway && cd voicegateway
   pip install -e ".[dev]" && pytest
   ```

4. Open a PR with `Closes #<issue>`.

[CONTRIBUTING.md](CONTRIBUTING.md) has the checklist and the deeper guides. Questions go to [Discord](https://discord.gg/ysFaF4uSB); security reports go through [SECURITY.md](SECURITY.md).

<a href="https://github.com/mahimailabs/voicegateway/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=mahimailabs/voicegateway&max=40&columns=10&anon=0" alt="Contributors" />
</a>

## License

Open core, following the [Langfuse](https://github.com/langfuse/langfuse) model. Everything outside `ee/` directories is [MIT](LICENSE). Code inside any `ee/` directory is under the [Enterprise Edition license](ee/LICENSE). There is no EE code today, and the self-hosted core stays fully usable without it.

Built in public by [Mahimai Raja](https://mahimai.dev), on [LiveKit Agents](https://github.com/livekit/agents), [Pipecat](https://github.com/pipecat-ai/pipecat) and [voice-prices](https://github.com/mahimailabs/voice-prices).

<!-- GitAds-Verify: B26PKZL6HHS6F2ZU9NAHRIA9OQHS919R -->

## GitAds Sponsored
[![Sponsored by GitAds](https://gitads.dev/v1/ad-serve?source=mahimailabs/voicegateway@github)](https://gitads.dev/v1/ad-track?source=mahimailabs/voicegateway@github)
