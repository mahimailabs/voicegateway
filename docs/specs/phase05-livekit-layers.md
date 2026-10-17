# Phase 05: Connect the LiveKit layers

## Goal

For a project added with its LiveKit URL, key and secret, the Media server, SIP, Egress, Ingress and Agent workers reads (`getMediaServer`, `getSip`, `getEgress`, `getIngress`, `getWorkers`, `getCloudAgents`) answer from that project's real LiveKit state instead of captured data. A layer the keys cannot reach, or a read that fails, says so; nothing unreported is shown as a value. This is the plan task "Connect LiveKit layers" (done when: each supported layer exposes real state and handles unavailable telemetry without invented values).

The phase is built as one spec and four adapter PRs, so no PR carries every layer: **05a** sampling plus the media server, **05b** SIP, **05c** egress and ingress (with #345), **05d** agent worker metrics and Cloud agents.

## Context

- PRODUCT.md principles 2 (unknown stays unknown) and 3 (never lose data quietly); the four layers (media server, phone bridge, agents, models) plus Egress and Ingress.
- ARCHITECTURE.md: Data Flow step 6 (the worker scrapes on a timer), step 7 (web reads storage to answer), Boundaries (web never writes ClickHouse, the worker never answers HTTP), External Services (if LiveKit is down, keep serving what was stored).
- Decision 0016: a project is one LiveKit project read from its three values; what they reach and what they do not (idle self-hosted workers, ServerInfo without joining a room, server CPU and memory on LiveKit Cloud). Observation by a hidden participant is opt-in and not part of this phase.
- Decision 0007: Redis also holds the locks for one-at-a-time jobs. Decision 0006: web and worker roles. Decision 0017: all four layers are OSS.
- `docs/api/openapi.yaml` and `docs/api/upstream-sources.md`: the six read shapes, their units and the native sources already audited (server-sdk-go 2.19.0, protocol v1.52.1).
- Phase 03: webhooks for rooms, participants, egress and ingress are already stored in ClickHouse `events`; this phase reads them for transition times.
- Phase 04 (foundation thread): calls and the first read handlers (`listCalls`, `getCall`) in 04c. Whichever HTTP read lands first creates `internal/api`; the other rebases onto it.

What each layer is read from, all with the project's own key and secret through LiveKit's Go clients:

| Layer | Native read (server-sdk-go v2.19.0) | Every | Not reachable with the keys |
| --- | --- | --- | --- |
| Media server | `RoomServiceClient.ListRooms` every 10 s; `ListParticipants` and `AgentDispatchClient.ListDispatch` per room only for rooms that are new or changed (participant count, or a stored room or participant webhook since the last read), with a full pass at most every 60 s | 10 s | `ServerInfo` (needs an RTC join, stays null); server CPU, memory, node list |
| SIP | `SIPClient.ListSIPInboundTrunk`, `ListSIPOutboundTrunk`, `ListSIPDispatchRule`; `PhoneNumberClient.ListPhoneNumbers` (LiveKit Cloud) | 60 s | call history (no API); passwords and PINs are read but dropped |
| Egress | `EgressClient.ListEgress` | 15 s | nothing beyond what LiveKit reports |
| Ingress | `IngressClient.ListIngress` | 15 s | endpoint creation time (no native field) |
| Cloud agents | `AgentClient.ListAgents`, `ListAgentVersions` (LiveKit Cloud only) | 60 s | self-hosted workers |
| Agent workers | Each self-hosted worker's own Prometheus endpoint (`lk_agents_worker_load`, `lk_agents_active_job_count`, labelled `nodename`, LiveKit Agents `telemetry/metrics.py`), at URLs the project lists | 15 s | idle workers whose endpoint is not listed; `loadThreshold` (a worker option, not exported) |

## In Scope

- **Sampling (`internal/scrape`, worker role).** One `Scraper` per layer, run on the intervals above for every project. A Redis lock per project and layer (0007) makes one worker read it at a time when several run. Each read uses the project's URL, key and secret (opened only through `pgstore.LiveKitSecret`), a 10 s deadline per call, and the SDK's own transport; no retry loop on top of the SDK's failover. A failed read is recorded with its class (`unauthenticated`, `permission_denied`, `unreachable`, `not_supported`, `error`) and never as an empty result.
- **Redaction before storage.** Only the contract's projection is stored, never the raw message: SIP `auth_username`, `auth_password`, dispatch rule `pin`, header values and `room_prefix` are dropped (presence and counts kept, as the contract says); ingress `stream_key` and any credentials or query values in its URL; egress request upload credentials and stream URLs reduced to scheme and host. Phone numbers in room names, track names and trunk numbers are cut to their last 4 digits before storage. A test feeds each read a message carrying every secret field and checks no stored byte contains them.
- **Storage (`migrations/clickhouse`, written by the worker only).** One table per layer of sampled projections (`room_samples` with dispatches and jobs inside each room, `participant_samples`, `sip_reads`, `egress_samples`, `ingress_samples`, `cloud_agent_reads`, `worker_samples`) plus `layer_reads` (project, layer, read at, outcome, error class) so coverage and failures are visible. Rows expire after 30 days (TTL). Migrations only add. Each migration takes the next free number on main at merge time; this spec names no fixed numbers.
- **Serving (`internal/api`, web role).** Each of the six reads is answered from ClickHouse: the latest successful read for configuration (SIP, Cloud agents), and for sampled layers the observations inside the watch windows, with `firstSeenAt`, `lastSeenAt` and status histories from the samples and from the stored webhooks (`egress_started|updated|ended`, `ingress_started|ended`, `room_*`, `participant_*`). `windows` lists the spans of continuous successful sampling with their interval. Every time is RFC 3339 from a reported value; LiveKit's units are converted as `upstream-sources.md` records (dispatch and job times are nanoseconds; room and participant times seconds, with the `_ms` fields preferred when set). A zero or absent source time is null.
- **Failure states.** VoiceGateway keeps serving what it has when LiveKit is down (ARCHITECTURE.md External Services). Each of the six reads gains an additive `sourceStatus` object: `lastReadAt` and `lastGoodReadAt` (RFC 3339, null if never) and `errorClass` (`unauthenticated`, `permission_denied`, `unreachable`, `not_supported` or `error`; null when the last read succeeded), computed from `layer_reads`. A read whose last good read is older than three intervals is stale, and the console shows its age and the error. Only a layer that has never been read successfully answers 503 `source_unavailable`, with a message naming the error class (for example "the API key has no SIP admin grant"), never an empty 200. A layer that does not exist for the project (Cloud agents or phone numbers on a self-hosted server) answers 200 with an empty list and a `source` that says why.
- **Contract and provenance.** `x-source` for these six schemas moves from the captured mocks to the native field (message and field in protocol v1.52.1, client method in server-sdk-go v2.19.0, Prometheus metric name and LiveKit Agents version). #345 is part of 05c: `Egress` gains `streams` (per output: host, status, error, started and ended) and `EGRESS_LIMIT_REACHED` reads as "recording cut short". `make api-check` passes. Changes stay additive.
- **Worker metrics endpoints.** `voicegateway project workers add|remove|list` stores a project's worker metric URLs in Postgres (`project_worker_endpoints`); no dashboard form yet. A listed endpoint that does not answer shows as unreachable, not as an idle or absent worker.
- **Console.** Once a read is served by Go, the console's mock for it is switched off with `VITE_MOCK_API` per read, agreed with the "Finish the console mock API" thread; the page code is not changed by this phase unless a contract addition (Egress streams) needs it.

## Out of Scope

- Joining a room as a hidden observer (0016 opt-in), `ServerInfo`, models and agent configuration.
- Any write to LiveKit (creating trunks, rules, egress); the keys are used read-only.
- Server CPU, memory and node lists (not exposed by LiveKit Cloud; on open-source LiveKit, Prometheus scraping of the server is a later phase).
- Calls, turns and `listCalls`/`getCall` (phase 04); alert evaluation over these samples (alerts phase).
- Login and the access policy behind `/api/v1` (whatever gate 04c places there covers these reads too).

## Constraints

- LiveKit's own code first: `github.com/livekit/server-sdk-go/v2` v2.19.0 clients and `livekit/protocol` v1.52.1 types; `utils.TimedVersionFromProto` for timed versions; `github.com/prometheus/common/expfmt` to parse worker metrics. Nothing hand-rolled where these have a method. v2.19.0 declares `go 1.26.8`, so the `go` and `toolchain` lines and the Dockerfile's Go image move to 1.26.8 first, in their own small PR. The SDK pins protocol `v1.51.1-0.20260910121219-271d9cde3897`; Go's module selection resolves it to our `v1.52.1`, which the provenance records.
- Boundaries: the worker reads LiveKit and writes ClickHouse; web never calls LiveKit on a request path and never writes ClickHouse. Flat packages: `internal/scrape`, `internal/api`. Wiring only in `internal/app`.
- Never log a secret, a token, a raw LiveKit message or an identity; ids, kinds, counts and error classes only. Never panic; wrap errors.
- Run the real thing: `livekit-server --dev` 1.13.3 backed by Redis (`--redis-host`, both on one Docker network, since SIP needs Redis) through testcontainers-go (rooms, participants, dispatch, SIP trunks and rules, egress and ingress lists), ClickHouse and Postgres containers, a Prometheus text endpoint from `httptest` serving the metrics LiveKit Agents names, `testing/synctest` for intervals and staleness, `-race`.

## Acceptance Checks

05a, sampling and the media server:

1. Given a room on `livekit-server --dev` with two participants and an agent dispatch, when the worker samples for 30 s, then `getMediaServer` returns that room by SID with both participants, their kinds, the dispatch with its job, and `createdAt`, `joinedAt` and `startedAt` equal to the server's values converted to RFC 3339 (measured by `TestMediaServerLive`).
2. Given a room that is deleted, when the next samples run, then the room stays in the read with its `lastSeenAt` at the last sample that saw it, and the window covering it is listed (measured by `TestRoomEndKeepsLastSeen`).
3. Given a participant whose join time and region LiveKit does not report, then both are null, never zero or the sample time (measured by `TestUnreportedStaysNull`).
4. Given good reads and then the project's secret replaced by a wrong one, when sampling runs past three intervals, then `getMediaServer` still answers 200 with the stored rooms, `sourceStatus.errorClass` is `unauthenticated` and `lastGoodReadAt` is the last good read; given a project never read successfully, it answers 503 `source_unavailable` naming the class, never 200 with an empty list (measured by `TestMediaServerSourceUnavailable`, with synctest).
5. Given two workers running, when a layer is due, then exactly one reads it per interval (measured by `TestOneScraperPerLayer`).

05b, SIP:

6. Given an inbound trunk with username and password, an outbound trunk with headers and a dispatch rule with a PIN, created on `livekit-server --dev`, when sampled, then `getSip` shows `auth: true`, `pin: true`, header names only, and no stored or served byte contains the password, the username, the PIN or a header value (measured by `TestSipRedacted`).
7. Given a key without the SIP admin grant and no earlier good read, then `getSip` answers 503 naming the missing grant; given earlier good reads, it answers 200 with them and `sourceStatus.errorClass` `permission_denied` (measured by `TestSipPermissionDenied`). `--dev` keys hold every grant, so the test puts LiveKit's own `permission_denied` Twirp error in front of the real server.

05c, egress and ingress:

8. Given egress webhooks already stored and an egress listed by `ListEgress`, then `getEgress` returns its status history from both, its outputs with `streams` per output (host only, never the stream key), and an `EGRESS_LIMIT_REACHED` egress as cut short (measured by `TestEgressLive`).
9. Given an ingress with a stream key, then `getIngress` returns its URL without the key or credentials, and `createdAt` null (measured by `TestIngressLive`).

05d, agent workers and Cloud agents:

10. Given two listed worker endpoints serving `lk_agents_worker_load` and `lk_agents_active_job_count`, when sampled for 60 s, then `getWorkers` returns both workers by `nodename` with samples carrying those values and `loadThreshold` null; given one endpoint that stops answering, its worker shows no new samples and the read names it unreachable, not idle (measured by `TestWorkersFromMetrics`, with synctest).
11. Given a self-hosted server URL, then `getCloudAgents` answers 200 with no agents and a `source` saying LiveKit Cloud agents do not apply, and no Cloud API call is made (measured by `TestCloudAgentsSelfHosted`).

Across the phase:

12. Given the six reads served by Go, when `make api-check` and the contract test validate them against `docs/api/openapi.yaml`, then every response matches its schema (measured by `TestReadsMatchContract`).

## Manual Check

With the LiveKit Cloud test project added (keys from `~/.config/voicegateway/loop.env`, never copied), run a call with the test agent and open the Media server, SIP, Egress, Ingress and Agent workers pages with the mock off: the room, its participants and dispatch appear while the call is live and remain afterwards with their last-seen time; SIP shows the project's trunks and rules with no secret; Cloud agents lists the project's deployments, or says none apply. Then change the stored secret to a wrong one and confirm each page says the source is unavailable instead of showing empty lists.

## Open Questions

- Intervals (10 s rooms, 15 s egress, ingress and workers, 60 s SIP and Cloud agents), the three-interval staleness rule and 30-day retention are recommendations. On LiveKit Cloud each read is an API request against the project's rate limits; change them before building if you want different numbers.
