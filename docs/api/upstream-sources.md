# LiveKit source audit for the console contract

This is a work-in-progress provenance audit for #321. Reading a capture proves what was displayed, but does not prove an upstream field's unit or availability. The final contract must distinguish the native field, the normalisation, and the captured evidence. No Go implementation or Go dependency pin is added by this PR.

## Sources consulted

The official [Docs MCP](https://docs.livekit.io/reference/developer-tools/docs-mcp/) is available at `https://docs.livekit.io/mcp/`. This chat did not expose it as a registered tool, so the same server was queried over Streamable HTTP (`initialize`, `tools/list`, `docs_search`, `get_pages`). The shared `.mcp.json` now includes `livekit-docs` for subsequent sessions. Browser login transport for VoiceGateway remains deliberately unselected; LiveKit's own JWT grants do not define VoiceGateway membership.

- Go SDK source inspected at commit [`6810cb347c9f4be6842d7d3f4f84cc9c0ab4aff6`](https://github.com/livekit/server-sdk-go/tree/6810cb347c9f4be6842d7d3f4f84cc9c0ab4aff6), reporting version 2.19.0. Its go.mod requires protocol v1.52.1. Inventory: all 151 Go files, including tests, examples and media helpers, parsed with Go's AST parser and their exported declarations reviewed. The service clients and the explicitly listed authentication, transport, participant, stream and timing helpers were also read in full. This declaration inventory guides later contextual review when adopting media/engine code.
- Protocol [`v1.52.1`](https://github.com/livekit/protocol/tree/v1.52.1), commit `863261643ad83c8c7fc55e46f6a0e6ae36c85e4e`, matching the console's recorded source audit. Generated Go definitions come from `protobufs/*.proto`; cite the message definitions rather than reverse engineering JSON defaults.
- Official [RoomService reference](https://docs.livekit.io/reference/server/server-apis/), [AgentDispatchService reference](https://docs.livekit.io/reference/agents/agent-dispatch-service-api/) and [observability data reference](https://docs.livekit.io/testing/observability/data/) retrieved through Docs MCP. The docs and versioned source are complementary; undocumented timestamp units still need source verification.

## Reuse native APIs

| Console input | Native Go facility | Contract boundary |
| --- | --- | --- |
| Room/participant reads | `RoomServiceClient.ListRooms`, `ListParticipants`, `GetParticipant` in `roomclient.go` | Project credentials stay server-side. ListRooms is active rooms, not historical call storage. |
| Dispatch/job reads | `AgentDispatchClient.ListDispatch`, `GetDispatch` in `agent_dispatch_client.go` | Native `AgentDispatch.state.jobs` and `JobState`; sampled history and caller/agent joins are VoiceGateway computations. |
| Cloud deployments/versions | `AgentClient.ListAgents`, `ListAgentVersions` in `agent_client.go` | CloudAgent is distinct from AgentDispatch. The client uses the Cloud agents endpoint and agent Admin grants; it does not list idle self-hosted workers. |
| Egress | `EgressClient.ListEgress` in `egressclient.go` | Return a safe projection of EgressInfo; never serialise request upload credentials. Native result arrays supersede deprecated oneof result fields. |
| Ingress | `IngressClient.ListIngress` in `ingressclient.go` | Project endpoint state and tracks; exclude stream keys. Native list request/response already have page tokens. |
| SIP trunks/rules | `SIPClient.ListSIPInboundTrunk`, `ListSIPOutboundTrunk`, `ListSIPDispatchRule` in `sipclient.go` | Separate inbound/outbound APIs; ListSIPTrunk is deprecated. `auth` means presence, not an exposed password. Provider hints and route diagrams are derived. |
| LiveKit phone numbers | `PhoneNumberClient.ListPhoneNumbers` in `phonenumberclient.go` | Native PhoneNumberService with SIP Admin grants; no public local-file API for phone numbers. |
| Verified webhooks | Protocol `webhook.ReceiveWebhookEvent` / `Receive` in `webhook/verifier.go` | Native JWT verification and constant-time body checksum comparison; do not implement custom signature verification or log the raw body. |
| Server API wiring | `NewLiveKitAPI`, service accessors, `WithURL`, `WithAPIKey`, `WithToken` in `livekitapi.go` | Native client shares HTTP transport/connection pool. Always supply the selected project's URL/credentials explicitly; environment fallback is not project isolation. Cloud agents and phone-number clients have separate constructors. |
| Service auth | `authBase.prepareContext` in `auth.go` | SDK applies native grants, signs tokens, adds request idempotency headers and preserves request context. Do not write another generic Twirp/auth wrapper. |
| Session/OTLP data | Agents observability upload and RemoteSession captures | Go server API reads do not produce per-turn STT/LLM/TTS timings. These fields must cite agent evidence and remain null when not reported. |

Additional SDK files read: `callback.go`, `transcription.go`, `track.go`, `publication.go`, `data.go`, `errors.go`, `protocolversion.go`, `private.go`, `utils.go`, `version.go`, `signalling/utils.go`, `logger.go`, `sampleprovider.go`, `localtrackoptions.go`, `agent_simulation_client.go`, `connectorclient.go`, `rpc.go`. Native RPC helpers already register JSON/protobuf handlers and carry request metadata, rather than requiring a custom RPC framework. AgentSimulation is a separate Cloud service and must not be assumed to implement VoiceGateway's self-hosted capacity test. `ExtractTranscriptionSegments` already converts native transcript segments. `TrackPublication.MimeType` already falls back to per-codec MIME where the top-level field is not set. `ServerError` aliases Twirp errors for native error classification. `GetDisconnectionReason` collapses several protocol reasons; use `Room.DisconnectReason()` when the console needs the original end reason.

The SDK's remaining RTC/media, RPC, stream, encryption and synchronisation facilities must be checked before later observer/recording work adds equivalents. None needs to be reimplemented to define these read schemas.

- Agents [`livekit-agents@1.8.6`](https://github.com/livekit/agents/tree/294de277c49ffa879ff7194a08f87c1a96a87a99), commit `294de277c49ffa879ff7194a08f87c1a96a87a99`: `livekit-agents/livekit/agents/metrics/base.py` read directly. The Docs MCP did not resolve the monorepo tag, so the tagged repository source was used.

## Verified distinctions

- `Room.creation_time` is seconds, while `creation_time_ms` is a separate field. `ParticipantInfo.joined_at` is seconds; `joined_at_ms` is milliseconds. Preserve which source supplied the normalised value.
- `Room.max_participants == 0` means unlimited. That is a known configuration sentinel, not an unknown participant measurement.
- `TrackInfo.disable_dtx` and `stereo` are deprecated in favour of `audio_features`; `disable_red` is a negative flag. A positive `dtx`/`red` projection must explain inversion instead of copying its boolean under a new name.
- `TimedVersion` carries `unix_micro` and `ticks`; native `utils.TimedVersionFromProto(...).Time()` converts it. Guard absent/zero versions before exposing a millisecond timestamp. Do not hand-roll the packed version arithmetic.
- ServerInfo reports edition, version, protocol, region and node id. `joinMs` is measured by the observer, not a ServerInfo field.
- AgentDeployment CPU and memory are string resource quantities. Keep Kubernetes-style units rather than assuming strings are percentages or bytes.
- A sampled status timestamp is the observer's read time, not necessarily the source's transition time. A sampled room's `lastSeenAt` is not proof of its end.
- Raw protobuf scalar defaults do not prove that a source reported a measurement. In particular, an absent timestamp must not become Unix epoch and an unreported latency must not become zero.
- The captured `llm_node` attribute `lk.response.ttft` and `tts_node` attribute `lk.response.ttfb` are seconds; the console turn projections multiply them by 1000 for `ttftMs` and `ttsMs`. `lk.e2e_latency`, `lk.end_of_turn_delay` and `lk.transcription_delay` likewise require explicit second-to-millisecond conversion. Per-request `lk.llm_metrics`/`lk.tts_metrics` are distinct measurements and must not silently replace per-node timings.

- Native metrics have explicit unknown/unavailable sentinels: `STTMetrics.audio_duration == 0.0` can mean unknown; streaming `STTMetrics.duration == 0.0` is not a measured STT processing latency. `EOUMetrics.end_of_utterance_delay` and `transcription_delay` are 0.0 when speech end was not detected. `LLMMetrics.ttft` and `RealtimeModelMetrics.ttft` use -1 when no token/audio token was generated. Normalise these unavailable cases to null when context establishes the sentinel; retain a genuinely observed zero. Providers also default several usage subcounts to zero, so do not claim a provider reported them without presence evidence.

Native transport and stream files also read: `connection_manager.go`, `regionurlprovider.go`, `failover.go`, `streams.go`. The SDK already owns RTC reconnect planning, Cloud region discovery, API failover and byte/text stream chunking/readers. Its API failover can reset long deadlines per attempt; later adapters must test their overall request budget and cancellation instead of layering an unconditional retry loop. Native byte streams provide the transport primitive for RemoteSession observation; no custom chunking protocol is needed.

`IngressInfo` has no native creation timestamp. The captured ingress `createdAt` is collector-retained test creation evidence and must be null when that evidence is absent.

## Documentation/source discrepancy

The Docs MCP AgentDispatchService table describes `AgentDispatchState.created_at` and `JobState.started_at` as Unix seconds. Native server commit [`3d45aa2e87a73ee0af799f744b5cca4a6e53f1f4`](https://github.com/livekit/livekit/tree/3d45aa2e87a73ee0af799f744b5cca4a6e53f1f4) writes dispatch `created_at` using `time.Now().UnixNano()` in `pkg/rtc/room.go` and job `started_at` using `now.UnixNano()` in `pkg/agent/worker.go`. The contract exposes the captured millisecond projections and records the nanosecond-to-millisecond conversion. Adapter tests must use native payload values and must not implement the conflicting documentation unit.

`PhoneNumber.e164_format` (not `phone_number`) is the native read-model field. Dispatch associations use `sip_dispatch_rule_ids`, with the deprecated singular field only as a compatibility fallback.

## Inventory findings and clock evidence

The full SDK declaration inventory includes native PCM tracks (`pkg/media`), Ogg reading, sample building, RTCP interceptors, frame/data encryption, Cloud-agent deployment/log streaming, and both legacy/new synchronisers. These facilities must be considered before adding equivalents. `pkg/synchronizer/interfaces.go` and `sessiontimeline.go` were read: native `SessionTimeline` maps observed RTP/RTCP sender-report evidence into a shared timeline, and returns errors when the session start, participant or reports are unavailable. Its one-way-delay estimate is inferred evidence, not a way to reconstruct missing capture anchors.

Native ingress commit [`ab27759fa9ee2598f78804101a3050e252d93a2c`](https://github.com/livekit/ingress/tree/ab27759fa9ee2598f78804101a3050e252d93a2c) assigns `IngressState.started_at`/`ended_at` using `time.Now().UnixNano()` in `pkg/params/params.go`; the contract records division by 1e9 for its seconds projection and keeps absent timestamps null.

## Contract audit result

The maintainer selected explicit source clocks with nullable alignment on 2026-10-16. `coverage.md` defines the runtime clock metadata, source-specific origins and comparison rules. No missing source anchors are reconstructed from capture start, upload time or the first zero offset. Native synchronisation estimates remain inferred evidence.

Source-unit conversions, nullable observations/media, dynamic maps, saved configuration, access scope and reader/route bindings have been reviewed against the retained captures and native sources above. Structural source coverage verifies attribution presence and pointer resolution; it does not prove server implementation, capture completeness or runtime authorisation. Contract tests also validate unknown versus observed/inferred alignment using the pinned JSON Schema validator. Go adapters and generated client types remain separate work.

## Phase 05d: agent workers and Cloud agents

- Cloud agents: `AgentClient.ListAgents` and `ListAgentVersions` in server-sdk-go v2.19.0 `agent_client.go`, both signed with `withAgentGrant{Admin: true}`; messages `AgentInfo`, `AgentDeployment` and `AgentVersion` in protocol v1.52.1 `protobufs/livekit_cloud_agent.proto`. `NewAgentClient` derives the agents host from any URL (or `LK_AGENTS_URL`), so the worker decides Cloud from the project URL itself: a host ending in `.livekit.cloud`. Any other URL is recorded as a good read where Cloud agents do not apply, with no Cloud API call. `AgentInfo.secrets`, `AgentVersion.attributes` and `owner`, and `AgentDeployment.events` are not kept. Timestamps are `google.protobuf.Timestamp`; absent, the epoch or earlier (lk's `0001-01-01`) is null. `version` of `---` or empty is null. proto3 `int32` replica counts cannot tell 0 from unset, so 0 is null; booleans are kept as reported.
- Agent workers: livekit-agents `livekit-agents/livekit/agents/telemetry/metrics.py` (default branch commit `01fb91f3e16407db6e2dbb4922014eeff8454b1c`, read through the LiveKit Docs MCP `code_search` on 2026-10-18) defines `lk_agents_worker_load` (`CPU_LOAD_GAUGE`, "Worker load percentage", a 0 to 1 ratio) and `lk_agents_active_job_count` (`RUNNING_JOB_GAUGE`, multiprocess `livesum`), both labelled `nodename`. They are parsed with `github.com/prometheus/common` v0.70.1 `expfmt.NewDecoder` and `dto.MetricFamily` from `prometheus/client_model`. `load_threshold` is a `WorkerOptions` value with no metric, so `loadThreshold` stays null.

### Project live invalidations (2026-10-20)

The live-events endpoint carries internal invalidation hints, not native LiveKit metrics. It uses
Go's net/http ResponseController for bounded SSE writes, the pinned go-redis v9.23.0 Subscribe /
ReceiveMessage / Publish methods for Redis pub/sub (decision 0007), and hey-api/openapi-ts 0.99.0's
generated SSE client in the browser. Redis hints are not durable; snapshot refetch on reconnect
and existing polling recover missed hints. Source metrics retain their original provenance above.

## Container resource collection (2026-10-20)

cAdvisor v0.60.6, lib/metrics/prometheus.go, supplies container CPU counters, memory working set,
quota/period, memory limit and start identity. CPU is either a total or unique per-CPU series;
never add both. Resource metrics carry the underlying stats timestamp. container_last_seen uses
exporter time, so it cannot establish measurement freshness. specMemoryValue exports unlimited
memory as zero; the API preserves the allocation as null. The pinned image and setup are recorded
in docs/container-resources.md. LiveKit Agents 1.8.7 telemetry/metrics.py and http_server.py do not
provide equivalent process-tree CPU and memory in the native multiprocess endpoint. Its worker
load remains a separate metric. Prometheus common v0.70.1 expfmt parses the exporter text.


## Recording and ingress issue evidence (2026-10-20)

LiveKit Docs MCP verified the pinned server v1.13.3 source at
https://github.com/livekit/livekit/blob/v1.13.3/pkg/service/ioservice_ingress.go:
UpdateIngressState invokes IngressEnded when the reported status changes to
ENDPOINT_ERROR, ENDPOINT_INACTIVE or ENDPOINT_COMPLETE. It invokes IngressStarted
on ENDPOINT_PUBLISHING. Therefore ingress_ended alone is not an error signal;
the nested state status must explicitly report ENDPOINT_ERROR. An absent state
cannot establish either failure or recovery. Native emitted-webhook verification
remains required; source inspection does not replace that check.

For terminal recording evidence, use protocol v1.52.1 EgressStatus and native
WebhookEvent identity. FAILED/ABORTED mean lost; LIMIT_REACHED means cut short.
Do not persist raw error text or output URLs in issue descriptions. Event identity
is scoped by organisation and project, independently of whether a call is linked.


Native ingress webhook verification now passes against pinned livekit-server v1.13.3:
TestIngressErrorEmitsSignedTerminalWebhook sends PUBLISHING, ERROR and PUBLISHING
through protocol v1.52.1 IOInfoClient over Redis. It receives the server's actual HTTP
webhooks and verifies signatures with webhook.ReceiveWebhookEvent. The error event
is ingress_ended with ENDPOINT_ERROR; recovery is ingress_started with PUBLISHING.
Both carry native event IDs and creation timestamps. This verifies native server
state/event propagation, not an encoder or ingress media pipeline's failure detection.
