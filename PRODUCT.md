# Product

<!-- impeccable:product-schema 1 -->

<!--
What VoiceGateway is and why it exists. No technical choices here; those live in ARCHITECTURE.md.
This file follows the PRODUCT.md layout impeccable (github.com/pbakaus/impeccable) reads, so /impeccable can use it as design context.
-->

## Platform

Web. A self-hosted Python package (`pip install voicegateway`) that serves a dashboard and HTTP API, plus an MCP server for coding agents. A hosted cloud edition is planned and lives outside this repository.

## Users

1. **Primary: engineers who build and run voice agents** on LiveKit, Pipecat or OpenRTC. They ship an agent, then get asked three questions they cannot answer from vendor consoles: did the call work, what did it cost, and how do we make it cheaper or faster. They open the dashboard after a bad call or before a pricing decision, and they need the answer in under a minute.
2. **Secondary: their leads and founders**, who read the same dashboard for spend per project and customer, and trust it only if it matches the invoices.
3. **Agents (roughly 10 percent of use):** Claude Code, Cursor, Codex and similar tools that query calls, costs and latency through the MCP server while a human works with them.

Humans are the target. When a choice trades human clarity against agent convenience, humans win; the MCP server exposes the same truth without its own features.

## Product Purpose

VoiceGateway is the open-source, 360 degree profiler for voice agents. One `attach()` line meters every STT, LLM and TTS call, and the server joins that with what happens below the agent (SIP trunk, SFU room, dispatch, node health), so a team sees one call end to end: whether it worked, where the time went and what each stage cost.

Success for V1: a new user goes from `pip install` to their first priced, timed call in the dashboard in under 10 minutes, and recorded cost reconciles with the provider invoice to within 1 percent.

## Positioning

The only open-source tool that profiles a voice call across every layer, from SIP and WebRTC down to the individual model, with per-modality pricing (audio seconds, tokens, characters) that reconciles against provider invoices. LLM observability tools such as Langfuse see tokens; voice platform consoles see one vendor. VoiceGateway sees the whole call, on the user's own keys and infrastructure.

## Operating Context

- Runs next to the user's agent: embedded SQLite in the agent process by default, or a self-hosted collector that a fleet of agents pushes to.
- Read on a laptop browser by engineers in the middle of debugging, often with a terminal and a coding agent open beside it.
- Sits beside LiveKit Cloud or self-hosted LiveKit, Pipecat, provider dashboards (OpenAI, Deepgram, Cartesia, ElevenLabs and others), and Grafana-style infra monitoring.
- Never sees audio. It records metrics, timings, transcripts only when the capture policy allows, and costs.

## Capabilities and Constraints

Core capabilities:

- Meter STT, LLM and TTS calls from the native framework instances the user already constructs, without bundling provider plugins.
- Price every call through voice-prices, per modality, with self-hosted models at zero cost.
- Profile the layers around inference: calls and call legs from LiveKit webhooks, SIP attributes, turns, dead air, tool calls, node samples.
- Reconcile recorded cost against provider usage exports (`voicegw reconcile`).
- Guard a provider with budget, fallback and rate limit (`guard()`).
- Serve a dashboard for humans and an MCP server for coding agents.

Hard limits:

- Self-hostable on one machine with no external services; SQLite is the default store.
- Python 3.11 to 3.13. `import voicegateway` must not import LiveKit or Pipecat.
- Open core: everything outside `ee/` is MIT, so the self-hosted product must stay complete without EE code.
- V1 public launch targeted for the end of October 2026.

## Out of Scope

- **Routing or proxying model traffic:** VoiceGateway observes the instances the user runs; it is not a gateway in front of providers. Text-only LLM apps are better served by LiteLLM.
- **Storing or replaying raw audio:** privacy and storage cost; only metrics and policy-allowed transcripts are kept.
- **Building or hosting agents:** LiveKit, Pipecat and OpenRTC do that.
- **Hosted multi-tenant features (orgs, billing, SSO) in this repository:** they belong to the cloud edition or to `ee/`.
- **Per-call RTP loss, jitter or MOS:** not observable server-side today, so no field pretends to show it.

## Failure Modes

- A call is metered but priced at the wrong rate, or a model silently prices at $0 because voice-prices has no match, and totals drift from the invoice.
- `attach()` raises or slows the agent, so a profiling bug becomes a production outage. Capture must fail open.
- A call that ran no inference (rejected SIP INVITE, early hangup) never appears, hiding the failures users most need to see.
- Store-and-forward to the collector drops records on restart, so fleet totals undercount.
- The dashboard renders an unknown value as zero (loss, latency), and users read a false "healthy".
- The MCP server answers from a different query path than the dashboard and the two disagree.

## Brand Commitments

- Name: VoiceGateway. CLI: `voicegw`. Domains: voicegateway.dev, docs.voicegateway.dev.
- Voice: short declarative sentences, sentence-case headings, no em dashes.
- Palette: signal on black with a single lavender accent, shared by the docs and landing page through `site/theme.css`.
- Built in public by Mahimai Raja under Mahimai Labs.

## Evidence on Hand

- Public README, docs site (`site/docs/content/docs/`: six pages) and a live demo dashboard with example data at voicegateway.dev/demo.
- Dashboard and cover screenshots in `site/docs/public/assets/`.
- Design specs in `docs/specs/`, including the end-to-end profiling scope.
- The README cost table uses example numbers, not a measured run.
- No customer testimonials, published benchmarks or measured reconciliation results exist yet. Do not invent them.

## Product Principles

1. **One line to start.** Anything that needs more than `attach()` and `voicegw serve` for a first result is a regression.
2. **Every number is honest.** Unknown stays unknown, never zero; every price traces to voice-prices or the user's rate card.
3. **Fewer, finished features.** A small set that is 100 percent valuable beats a broad set that is half done.
4. **Humans first, agents too.** Design and clarity for people decide the UI; the MCP server gives agents the same facts.
5. **Profiling never hurts the call.** Capture is async and fails open.

## Accessibility & Inclusion

WCAG 2.2 AA for the dashboard, docs and landing page: 4.5:1 text contrast in light and dark themes, full keyboard navigation, visible focus, and charts that carry their values in text, not color alone.
