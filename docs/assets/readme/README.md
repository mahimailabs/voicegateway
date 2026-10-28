# README presentation assets

Reference: [Langfuse's README](https://github.com/langfuse/langfuse/blob/main/README.md), selected by the maintainer on 2026-10-16. Borrowed structure: wide banner with a product glimpse, two navigation rows, compact factual badges, illustrated features, a runnable quickstart and a real inspection example. Artwork, copy and product captures are VoiceGateway's. No adoption claims or integration inventory were borrowed.

| Reference element | VoiceGateway mapping |
| --- | --- |
| Hero and trace glimpse | Existing wordmark, product promise and captured call inspector |
| Cloud / self-host / demo links | Self-host / docs / website / V1 roadmap |
| Illustrated capabilities | Captured Overview and Live wire; reported data stays distinct from unavailable measurements |
| Deployment section | Installer quickstart, then the captured-data console |
| Trace example | Captured caller turn with end-of-turn, first-token and first-audio timings |

Each image has explicit light/dark files. The root README selects them with `picture` and `prefers-color-scheme`, with light as the fallback. SVG diagrams are standalone; screenshots and hero banners are JPEG. Every new asset is below the repository's 250 KiB file cap.

## Captures

Captured on 2026-10-28 from main at `aecb6121`, with `make dev` (the mock API serving the committed LiveKit captures) and Playwright at 1440 × 900, once per theme. No displayed measurement was edited. The mock API has no account, so the sidebar's account row is hidden during capture.

- Hero (`hero-light.webp`, `hero-dark.webp`): an animated WebP. From `/org/default/project/default-project/calls`, open `vg-capture-1791863749`, choose Diagnosis, then Next turn, and scroll the breakdown. Record with Playwright's `recordVideo`, then convert the last 15.4 seconds with `ffmpeg -vf "fps=8,scale=960:-1:flags=lanczos" -c:v libwebp_anim -q:v 60 -compression_level 6 -loop 0` (the dark file at the same settings stays under the cap). If your ffmpeg has no libwebp, write the same frames as PNGs and encode them with `img2webp -loop 0 -lossy -q 60 -m 6 -d 125`, which is how the current files were made. Drop any leading frames that still show the account row, since it is hidden only after the page settles.
- Overview: `/org/default/project/default-project/overview`.
- Call (`call-*.jpg`): `/org/default/project/default-project/calls/vg-capture-1791863749?callView=diagnosis`, which opens on the slowest turn.
- JPEG stills: `ffmpeg -i <png> -q:v 4 <jpg>`.

The docs site's `site/docs/public/screens/` holds the same captures plus Calls, Issues, Alerts, Agent workers, the call's Conversation view and Settings (`?settings=thresholds`, `?settings=data`).

## Banner source

`tools/readme-banner.html` composes the wordmark and headline for the GitHub social card (see below); its wide format, with the captured call image, was the README banner until 2026-10-28. Serve the repository locally, open it with `?format=social&theme=light` or `dark`, and capture its bounds. The README itself now opens with the animated hero instead of this banner.

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

## Components diagram

`components.reladraw` lays out the components in ARCHITECTURE.md: web and worker over Postgres, the S3 bucket, Redis and ClickHouse, with LiveKit, the voice agent and the dashboard feeding web. Brand marks in `icons/` are from [Simple Icons](https://simpleicons.org) 16.35.0 (CC0); the bucket is reladraw's own `package` icon. `tools/readme-diagram` renders `components-light.svg` and `components-dark.svg` with reladraw's layout, Rough.js ink and outlined Kalam lettering, so the SVGs need no font or script:

```bash
npm --prefix tools/readme-diagram ci
npm --prefix tools/readme-diagram run build
```

Kalam is by Indian Type Foundry under the SIL Open Font License (`tools/readme-diagram/Kalam-OFL.txt`); the checked-in font is a subset of printable ASCII plus the middle dot, so labels must stay within it.
