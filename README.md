<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="dashboard/src/assets/brand/wordmark-dark.svg" />
  <img src="dashboard/src/assets/brand/wordmark.svg" alt="VoiceGateway" width="336" height="56" />
</picture>

### One call. The whole story.

**The open-source profiler for LiveKit voice agents.**<br />
From the phone line to the model, find where a call went wrong.

[Explore the console](#try-the-console) · [V1 roadmap](docs/progress.md) · [Join in](#build-with-us)

</div>

![VoiceGateway console preview: answer-time gauge, turn distribution and Live wire, shown with captured LiveKit data.](docs/assets/voicegateway-teaser.gif)

[View the still image](docs/assets/voicegateway-teaser-poster.jpg)

> **V1 is being built in public.** This branch contains the new console with captured LiveKit data. The Go backend and hosted Cloud service are in development. The published Python release is the earlier product.

## Follow the call, not six log files

![A LiveKit call crosses SIP, the media server, the agent and its STT, LLM and TTS models. VoiceGateway collects their telemetry into one timeline with transcript, latency, usage and events. It stays outside the audio path.](docs/assets/voicegateway-flow.svg)

| When you ask… | Open… |
| --- | --- |
| **Why did it take so long to answer?** | A call → transcript, turn anatomy and trace |
| **The model looks fine. What else broke?** | SIP, media server and agent workers |
| **Is this happening across calls?** | Issues, alert rules and the live view |

*The preview uses captured data; Live replays it. Server-backed actions and notifications arrive with the backend.*

## Try the console

Requires Node.js **22.12+** and npm. No API keys needed for the preview.

```bash
git clone --branch feat/phase01-console https://github.com/mahimailabs/voicegateway.git
cd voicegateway/dashboard
npm ci
npm run dev
```

Open **[localhost:5180](http://localhost:5180)**. Pick a call, inspect a turn, follow its trace.

## The road to V1

| Preview now | Building for V1 |
| --- | --- |
| Console · captured calls · infrastructure views | Live ingest · durable storage · project access |
| Issues · alert rules · settings · embed previews | Notifications · recordings · MCP · capacity tests |
| Same product, two ways to run it | Free self-hosting **or** paid VoiceGateway Cloud |

Cloud will be self-serve: **sign up → Stripe checkout → automatic workspace access**. No manual activation. Provider usage is measured in tokens, seconds and characters; V1 does not estimate model costs.

[Product](PRODUCT.md) · [Architecture](ARCHITECTURE.md) · [Decisions](docs/decisions/) · [Current progress](docs/progress.md)

## Build with us

**[Pick an issue](https://github.com/mahimailabs/voicegateway/issues)** → agree on scope → open a PR against the V1 branch.

Start with the [console guide](dashboard/README.md) and [current spec](docs/specs/phase01-console.md). From the repository root, run `npm --prefix dashboard ci`, then `make dev` to open the console and `make lint` for lint and TypeScript checks. `make test` runs helper regression tests; calls-list and call-detail UI tests remain issue #324. Use `make help` for all console and landing targets. The [contributor guide](CONTRIBUTING.md) still includes the legacy Python workflow; follow the current spec for V1 work.

[Discord](https://discord.gg/ysFaF4uSB) · [Report a bug](https://github.com/mahimailabs/voicegateway/issues/new/choose) · [Security](SECURITY.md)

**Free to self-host.** [MIT](LICENSE) outside `ee/`; [Enterprise license](ee/LICENSE) inside it. Cloud hosts the same product. [Read the boundary](docs/decisions/0017-oss-enterprise-cloud-boundary.md).

<!-- GitAds-Verify: B26PKZL6HHS6F2ZU9NAHRIA9OQHS919R -->
<details>
<summary>Sponsors</summary>

[![Sponsored by GitAds](https://gitads.dev/v1/ad-serve?source=mahimailabs/voicegateway@github)](https://gitads.dev/v1/ad-track?source=mahimailabs/voicegateway@github)

</details>
