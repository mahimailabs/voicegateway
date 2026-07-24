# Server page: LiveKit deployment topology, cost-annotated

Date: 2026-07-23
Status: Phase 1 shipped (rooms + agents + fleet, cost-annotated); Phase 2 shipped
(SIP + Egress + Ingress). Both on PR #147. Pipecat deferred.
Scope decision: full control plane, new "Server" nav item (OSS dashboard, LiveKit first; Pipecat deferred)

## Problem

The OSS dashboard has no view of the LiveKit deployment the metered agents run on.
The user wants a left-nav "Server" page with a Railway-style component view
(SFU, SIP, Egress, Ingress).

## The tension (and the resolution)

VoiceGateway is a cost/quality profiler, not an infrastructure monitor. A raw
topology-of-boxes duplicates LiveKit's own console. LiveKit's console, however,
does not know cost. So the page earns its place by joining the live deployment
map to VG's own metered cost and latency per room / per agent. That join is the
only reason to build this instead of linking to LiveKit's console.

## Honesty guardrails (non-negotiable)

VG shows only data it actually has. It must NOT draw:

- SFU node health, live load bars, or green/red node dots (VG has no node metrics).
- `loss_pct` as a measured value (the probe path hardcodes 0.0).
- Any per-component ($/SIP-trunk, $/egress) cost (those components emit no
  attach()/guard() calls, so VG has no cost for them).

The SFU tile shows live room/participant totals (real, cheap) plus a timestamped
"last probe" line and a Run-probe button into Diagnostics. No invented gauge.

Secret whitelist (Phase 2): the LiveKit egress/ingress/sip proto messages carry
credentials (auth_password, auth_username, stream_key, RTMP url, allowed_addresses,
headers, metadata). The engine row dataclasses copy ONLY non-sensitive identifiers,
so no secret can reach the dashboard JSON. Enforced by allowlist-by-construction
and asserted in tests that build a proto WITH the secret set.

## Data sources (grounded in the repo)

Real and reachable today (LiveKit Server API, creds already resolved by
`livekit_diag/config.py resolve_creds`, LIVEKIT_URL/API_KEY/API_SECRET):

- Rooms + in-room participants + per-room agents (active vs dispatched), human
  occupancy, agent age — `livekit_diag/admin.py list_agents` (zero new code).
- Connection status + server URL — already shipped (`/api/diagnostics/creds`).

Real, VG-native (not LiveKit):

- Fleet roster (idle/busy/offline, region, host, version, active_sessions) —
  workers table via the collector heartbeat `GET /v1/agents`; requires
  VOICEGW_COLLECTOR_URL + VOICEGW_API_KEY. Degrade gracefully when unset.
- Per-room metered cost / request count / p95 (24h) —
  `request_log_repository.get_requests_for_room` (exists, tested).

Real but net-new read code (no new pip dependency; `livekit-api` already ships
the service clients):

- Egress jobs — `EgressService.list_egress`.
- Ingress endpoints — `IngressService.list_ingress`.
- SIP inbound/outbound trunks + dispatch rules — `SIPService.list_sip_*`.

Cannot get (and will not fake): SFU node CPU/health, per-node distribution,
Prometheus-style time series, which LiveKit URL each agent connects to
(LIVEKIT_URL is not stamped on metering rows).

## Backend

New module `src/voicegateway/server/api/dashboard/server.py`:

- `router = APIRouter(prefix="/server", tags=["dashboard"])`, wired into
  `server/routes.py` (import + `dashboard_router.include_router(...)`), gated by
  `Depends(require_scope(ADMIN_SCOPE))` (infra data, matches Diagnostics; no-op
  when no API keys configured).
- `GET /api/server/overview` returns a snapshot with each section shaped
  `{ok: bool, data, error?: str}` so one failing read does not blank the page.
  Control-plane lists are cheap and non-billing; fetch them concurrently.

Engine additions to `livekit_diag/admin.py LiveKitAdmin` (read-only): `list_egress`,
`list_ingress`, `list_sip_inbound_trunks`, `list_sip_outbound_trunks`,
`list_sip_dispatch_rules`, each returning a small dataclass. Exact method and
request-type names confirmed against the LiveKit Docs MCP before implementation,
not from memory. Handle the `[livekit]` extra being absent with a clear install
hint (same lazy-import + graceful-degrade as Diagnostics).

## Frontend

- `lib/nav.ts` — add `{to: '/server', label: 'Server', id: 'server'}` to the
  Monitor group (sidebar + command palette derive automatically).
- `App.tsx` — import `Server`, add `<Route path="/server" element={<Server />} />`.
- `components/NavIcon.tsx` — add a `server` glyph (server-rack) keyed by id.
- `pages/Server.tsx` — PageHeader accent "blue"; sections as `vg-card`s;
  a component grid of neo-cards with real counts + control-plane state;
  rooms table annotated with cost/p95; graceful empty states per section.
- `lib/api.ts` — `fetchServerOverview()`; `lib/types.ts` — `ServerOverview`.
- Rebuild `src/dashboard/frontend/dist` (the daemon serves the compiled bundle).

## Phasing (each its own PR)

- Phase 1 (spine): nav + route + page + `/api/server/overview` with
  connection + rooms(+cost) + in-room agents + fleet roster. Existing reads only.
- Phase 2 (control plane): SIP + Egress + Ingress read wrappers, endpoint
  sections, UI tiles.

## Tests

- Engine: unit tests for the new `list_*` wrappers with a mocked LiveKitAPI.
- Backend: `/api/server/overview` test with a fake LiveKit client + a seeded
  SQLite store, asserting per-section shape and graceful degrade when creds or
  collector are absent, and admin-scope behavior. Follow the diagnostics test
  patterns.
- Frontend: type-check (`tsc`) and build.

## Out of scope

Pipecat topology, SFU node metrics / Prometheus, per-component cost attribution,
persisted topology time series, richer SIP call metadata (from/to number, trunk id).
