---
title: "The Server page"
description: "A section-by-section reference for the dashboard's Server screen: live rooms, the worker fleet, egress, ingress, and SIP trunks, plus what it cannot show you."
---
The Server page (`/server` in the dashboard SPA, backed by `GET /api/server/overview`) is a
read-only snapshot of the LiveKit deployment your agents run on, annotated with VoiceGateway's own
metered cost. LiveKit's own console shows the same topology but not the cost; this page joins the
two, using cheap control-plane reads only: no synthetic probes, no billed calls. See
[What you can profile](/guide/what-you-can-profile) for how this fits against the agent and SIP
layers. Gated behind the admin scope like Diagnostics: a no-op locally with no API keys
configured, enforced once auth is enabled.

To run the process that serves this page, install `voicegateway[livekit,dashboard]`. Every
`voicegw` subcommand, including `serve` and `dashboard`, imports the LiveKit diagnostics modules at
startup, so `[livekit]` is required before the reads below even run; `[dashboard]` brings FastAPI,
uvicorn, and the built SPA. See [Installation](/guide/installation) for the full extras matrix.

## Connection

A badge reports one of four states:

- **Not configured**: no `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` (or a
  `livekit:` block in `voicegw.yaml`) resolved. A setup hint shows; nothing below is queried.
- **Configured**: credentials resolved, but the `livekit` extra isn't installed. The page says
  "LiveKit SDK not installed. Install voicegateway[livekit]."
- **Unreachable**: credentials and SDK both present, but every control-plane read failed.
- **Connected**: at least one control-plane read succeeded.

Reachability is reported true or false only when a read was actually attempted; an unmeasured
deployment is never mislabeled unreachable.

## Rooms: the only cost-annotated section

Live rooms and their in-room or dispatched agents come from the LiveKit Server API. Each room is
enriched with VoiceGateway's own metered numbers over the trailing 24 hours: cost in USD, request
count, and p95 latency, computed from local request records with the same percentile helper the
Latency and Agents pages use: a database read, not a provider invoice. A failed read degrades to
zeros rather than blanking the row. Nothing else on the page carries a cost number. Unreachable
LiveKit shows that instead of listing rooms.

## SIP, egress, ingress

A **read-only wrapper over `livekit.api`**: VoiceGateway does not create, edit, or delete trunks,
dispatch rules, egress jobs, or ingress endpoints here.

| Panel | Shows |
|---|---|
| SIP | Inbound trunks (name, numbers); outbound trunks (name, address, transport, numbers); dispatch rules (name, trunks it applies to, or "any") |
| Egress | Recording/streaming jobs: status, room, source, start time |
| Ingress | WHIP/RTMP/URL endpoints: name, input type, room, status |

Auth fields, stream keys, and passwords are never read into the dashboard. If your deployment
doesn't run one of these services, that panel alone shows "not available on this deployment" (or
whatever the control plane returned); the rest of the page is unaffected, since each section fails
independently. All three render only once LiveKit is configured. For SIP capacity and
answer-latency evidence under load, see [`voicegw loadtest`](/cli/loadtest); this page only lists
what's wired up.

## Fleet

The worker roster is VoiceGateway-native, not a LiveKit read: the LiveKit Server API cannot report
idle, registered-but-undispatched workers. It comes from your agents calling
`register_worker(...)`, heartbeating to this collector (`VOICEGW_COLLECTOR_URL` +
`VOICEGW_API_KEY`). Columns: agent, status (idle/busy/offline), region, host, version, active
sessions, memory percent (RSS over the worker's memory ceiling), last seen. Empty when nothing has
heartbeated, but also when the roster read itself fails: a failed read degrades to the same empty
list, and the dashboard doesn't currently surface which one happened.

## What this page cannot show you

No SFU node health or load bars: VoiceGateway never fabricates a number it hasn't measured. The
only SFU number it can produce is a timestamped probe from
[`voicegw livekit sfu`](/cli/livekit), which measures connection quality and costs nothing
because it never invokes STT, LLM, or TTS. For SFU host capacity over time (CPU, memory,
connection counts), see [Node metrics](/guide/node-metrics) instead.
