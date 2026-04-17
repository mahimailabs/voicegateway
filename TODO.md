# VoiceGateway TODOs

Items identified but not in scope for the current pass.

## Priority: High

- [ ] Add more STT/LLM/TTS providers (Rime, Speechmatics, Fish Audio, Hume AI)
- [ ] Migrate to VitePress docs site at docs.voicegateway.dev
- [ ] Add frontend unit tests (Vitest) to the Vite dashboard
- [ ] Evaluate whether `all` extra should include `mcp` (user expectation question)

## Priority: Medium

- [ ] Add provider connection caching (avoid re-testing on every page load)
- [ ] Rate limiting on MCP HTTP/SSE transport (prevent abuse)
- [ ] Config file hot-reload (watch voicegw.yaml for changes)
- [ ] Multi-tenancy (per-team namespacing of projects)

## Priority: Low

- [ ] Dark mode for dashboard
- [ ] Keyboard shortcuts in the dashboard
- [ ] Export-as-Docker-compose generator for a given project

## Already addressed in previous passes

- [x] Latency instrumentation (reliability pass)
- [x] Budget enforcement (reliability pass)
- [x] MCP server with 17 tools (MCP pass)
- [x] API key encryption (GUI management pass)
- [x] Managed resources persist across restarts (GUI management pass)
- [x] HTTP CRUD endpoints (GUI management pass)
- [x] Audit log (GUI management pass)
- [x] Settings page with Providers/Models/General tabs (GUI management pass)
- [x] Projects page with list/create views (GUI management pass)
- [x] Source badges throughout the dashboard (GUI management pass)
