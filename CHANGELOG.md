# Changelog

All notable changes to VoiceGateway are documented here. This project
follows [Semantic Versioning](https://semver.org/) and
[Conventional Commits](https://www.conventionalcommits.org/).

## v0.7.0: voice-prices pricing backend

Pricing moves from `pydantic/genai-prices` to
[`voice-prices`](https://github.com/mahimailabs/voice-prices), a fork that
prices all three modalities (LLM, STT, and TTS) from one source.

### Changed

- **Single pricing backend.** LLM, STT, and TTS costs now all resolve
  through `voice-prices`. The hand-maintained local STT/TTS rate catalogs
  are retired; `voice-prices` owns rates and freshness (each entry carries
  `prices_checked` and `pricing_source_url`).
- **Pricing-source attribution.** Cloud-priced records are tagged
  `voice-prices@<version>`; self-hosted (`local/*`, `ollama/*`) models are
  tagged `voicegateway-local`; unknown models stay unpriced. The catalog-only
  `oldest_entry_date` field is dropped from the `/v1/status` and `/api/status`
  responses (`voice-prices` owns freshness).
- **STT and TTS rates** now follow `voice-prices` and may differ from the
  previous local-catalog estimates. Reconcile against your provider invoices.

### Dependencies

- `genai-prices` replaced by `voice-prices>=0.0.8,<0.1`.

## v0.6.0: first public release

The first public release of VoiceGateway. A self-hosted gateway for
LiveKit voice agents that tracks costs per modality (audio-minutes for
STT, tokens for LLM, characters for TTS) and reconciles logged costs
against provider invoices.

### What you get out of the box

- **Drop-in replacement for `livekit.agents.inference`.** Swap one
  import line and your agent code keeps running:
  `from voicegateway.inference import STT, LLM, TTS`. Cost tracking,
  latency monitoring, and per-session correlation happen transparently.
- **Cost tracking per modality.** LLM cost per 1k tokens (prices from
  `pydantic/genai-prices`, 1100+ models). STT cost per audio-minute and
  TTS cost per character (catalog with source-date metadata). Cached
  LLM input tokens are billed at the provider's cache-read discount
  rate (OpenAI 50%, Anthropic ~10%) by surfacing LiveKit's
  `prompt_cached_tokens` through to `genai-prices.cache_read_tokens`.
- **Background daemon.** `voicegw onboard` runs a five-question wizard,
  writes `voicegw.yaml`, registers a user-scoped service (LaunchAgent on
  macOS, `systemd --user` unit on Linux, Scheduled Task on Windows),
  and starts the daemon.
- **Web dashboard and HTTP API on a single port.** The daemon serves
  the React dashboard at `/`, the dashboard API at `/api/*`, and the
  public HTTP API at `/v1/*`. `voicegw dashboard` opens your browser
  at the daemon URL.
- **Reconciliation tooling.** `voicegw export-costs` and
  `voicegw reconcile` compare your logged costs against your provider's
  usage export. Per-row `pricing_source` attribution shows exactly
  which catalog or version priced each call.
- **MCP server for agent-managed config.** Seventeen tools over stdio
  and HTTP/SSE let Claude Code, Cursor, Codex, and Cline manage
  providers, projects, budgets, and queries conversationally.
- **Multi-tenant attribution.** Virtual API keys carry a tenant id so
  sessions auto-tag for per-customer reporting. Virtual keys expose
  their plaintext exactly once at creation and support soft revocation.
- **Cross-modality routing.** Per-session, lowest-predicted-total-
  latency selection of (STT, LLM, TTS) from per-project rosters, with
  observed latency feeding the predictor.
- **White-label branding.** Per-project logo, accent color, and
  product name. The dashboard chrome reflects the brand for users
  scoped to that project.
- **Conversation replay.** Per-modality time-ordered capture of every
  request, with retention windows configurable per project.
- **Guardrails.** Per-project policy overlay (PII categories, action
  enforcement), audit log of fired and bypassed events.

### Install

```bash
curl -fsSL https://voicegateway.mahimai.ca/install.sh | bash
```

Or:

```bash
pipx install 'voicegateway[cloud,dashboard]'
uv tool install 'voicegateway[cloud,dashboard]'
```

See [Get started](https://voicegateway.mahimai.ca/docs/get-started)
for the full first-run flow.
