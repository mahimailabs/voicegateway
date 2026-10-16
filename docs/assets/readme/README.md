# README presentation assets

Reference: [Langfuse's README](https://github.com/langfuse/langfuse/blob/main/README.md), selected by the maintainer on 2026-10-16. Borrowed structure: wide banner with a product glimpse, two navigation rows, compact factual badges, illustrated features, a runnable quickstart and a real inspection example. Artwork, copy and product captures are VoiceGateway's. No adoption claims or integration inventory were borrowed.

| Reference element | VoiceGateway mapping |
| --- | --- |
| Hero and trace glimpse | Existing wordmark, product promise and captured call inspector |
| Cloud / self-host / demo links | Console preview / website / V1 roadmap; Cloud is not yet available |
| Illustrated capabilities | Captured Overview and Live wire; reported data stays distinct from unavailable measurements |
| Deployment section | Current Node 24 console quickstart; backend installation remains planned |
| Trace example | Captured caller turn with end-of-turn, first-token and first-audio timings |

Each image has explicit light/dark files. The root README selects them with `picture` and `prefers-color-scheme`, with light as the fallback. SVG diagrams are standalone; screenshots and hero banners are JPEG. Every new asset is below the repository's 250 KiB file cap.

## Captures

Captured from main at `551df3ca` on 2026-10-16, using the existing console components and committed LiveKit fixtures. No displayed measurements were edited. These are preview observations, not a live backend.

- Overview: `/org/default/project/default-project/overview`, crop the content area to keep the gauge, turn distribution and Live wire together.
- Call inspector: `/org/default/project/default-project/calls/console-62f58fc1?turn=4-caller`, crop the actual dialog. The selected caller asks about Toronto. Recorded audio is unavailable in this example.
- Capture each view once per theme using the console's theme toggle. Transcripts may have different scroll positions across fresh navigation; both images select the same turn.

## Banner source

`tools/readme-banner.html` composes the existing outlined wordmark, headline and captured call image. Serve the repository locally, open this file with `?theme=light` and `?theme=dark`, and capture its 1800 × 580 banner bounds into `hero-light.jpg` and `hero-dark.jpg`. This source is static presentation artwork; it adds no product UI or runtime dependency. Keep the captured image literal, without redrawing its controls or replacing its numbers.

## Diagram source

`call-path.reladraw` describes the observation boundary. Models are reached by the voice agent; VoiceGateway receives telemetry outside the audio path. This is the V1 design, not proof of implemented ingestion.

Render from the repository root with the pinned scratch tool:

```bash
npx --yes reladraw@0.16.0 docs/assets/readme/call-path.reladraw --theme light -o docs/assets/readme/call-path-light.svg
npx --yes reladraw@0.16.0 docs/assets/readme/call-path.reladraw --theme dark -o docs/assets/readme/call-path-dark.svg
```

The maintainer also referenced [local-livekit](https://github.com/mahimairaja/local-livekit). Its current pipeline asset is rendered from HTML/Rough.js; this diagram uses reladraw, with explicit light/dark output and the VoiceGateway blue border.

## GitHub social preview

`social-light.jpg` and `social-dark.jpg` are 1280 × 640 JPEG exports from the same banner source with `?format=social&theme=light` or `?format=social&theme=dark`. The social format uses legible typography instead of a small UI capture. Both exports stay below 45 KB and the repository file cap.

GitHub stores one fixed social image, not theme-adaptive alternatives. The light export was uploaded to `mahimailabs/voicegateway` on 2026-10-16 and verified in Settings after reload. The dark export is retained for manual reuse. This repository setting is independent of merging the artwork PR. The card states V1 is in development and makes no cost, scoring or non-LiveKit framework claims.
