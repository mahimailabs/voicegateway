# LiveKit source audit for the console contract

This is a work-in-progress provenance audit for #321. Reading a capture proves what was displayed, but does not prove an upstream field's unit or availability. The final contract must distinguish the native field, the normalisation, and the captured evidence. No Go implementation or Go dependency pin is added by this PR.

## Sources consulted

The official [Docs MCP](https://docs.livekit.io/reference/developer-tools/docs-mcp/) is available at `https://docs.livekit.io/mcp/`. This chat did not expose it as a registered tool, so the same server was queried over Streamable HTTP (`initialize`, `tools/list`, `docs_search`, `get_pages`). The shared `.mcp.json` now includes `livekit-docs` for subsequent sessions. Browser login transport for VoiceGateway remains deliberately unselected; LiveKit's own JWT grants do not define VoiceGateway membership.

- Go SDK source inspected at commit [`6810cb347c9f4be6842d7d3f4f84cc9c0ab4aff6`](https://github.com/livekit/server-sdk-go/tree/6810cb347c9f4be6842d7d3f4f84cc9c0ab4aff6), reporting version 2.19.0. Its go.mod requires protocol v1.52.1. Inventory: 151 Go files, including tests, examples and media helpers. Service clients and their shared authentication/HTTP entry point have been read; the broader file-by-file review is ongoing.
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
- A sampled status timestamp is the observer's read time, not necessarily the source's transition time. A sampled room's `lastSeen` is not proof of its end.
- Raw protobuf scalar defaults do not prove that a source reported a measurement. In particular, an absent timestamp must not become Unix epoch and an unreported latency must not become zero.
- The captured `llm_node` attribute `lk.response.ttft` and `tts_node` attribute `lk.response.ttfb` are seconds; the console turn projections multiply them by 1000 for `ttftMs` and `ttsMs`. `lk.e2e_latency`, `lk.end_of_turn_delay` and `lk.transcription_delay` likewise require explicit second-to-millisecond conversion. Per-request `lk.llm_metrics`/`lk.tts_metrics` are distinct measurements and must not silently replace per-node timings.

- Native metrics have explicit unknown/unavailable sentinels: `STTMetrics.audio_duration == 0.0` can mean unknown; streaming `STTMetrics.duration == 0.0` is not a measured STT processing latency. `EOUMetrics.end_of_utterance_delay` and `transcription_delay` are 0.0 when speech end was not detected. `LLMMetrics.ttft` and `RealtimeModelMetrics.ttft` use -1 when no token/audio token was generated. Normalise these unavailable cases to null when context establishes the sentinel; retain a genuinely observed zero. Providers also default several usage subcounts to zero, so do not claim a provider reported them without presence evidence.

## Documentation/source discrepancy

The Docs MCP AgentDispatchService table describes `AgentDispatchState.created_at` and `JobState.started_at` as Unix seconds. Native server commit [`3d45aa2e87a73ee0af799f744b5cca4a6e53f1f4`](https://github.com/livekit/livekit/tree/3d45aa2e87a73ee0af799f744b5cca4a6e53f1f4) writes dispatch `created_at` using `time.Now().UnixNano()` in `pkg/rtc/room.go` and job `started_at` using `now.UnixNano()` in `pkg/agent/worker.go`. The contract exposes the captured millisecond projections and records the nanosecond-to-millisecond conversion. Adapter tests must use native payload values and must not implement the conflicting documentation unit.

`PhoneNumber.e164_format` (not `phone_number`) is the native read-model field. Dispatch associations use `sip_dispatch_rule_ids`, with the deprecated singular field only as a compatibility fallback.

## Remaining audit before review

Finish the SDK file review and field-by-field schema audit, particularly agent dispatch/job and egress/ingress timestamp conversions, TimedVersion units, opt-in observation, nullable media and dynamic maps. Complete configuration/local-state schemas and the machine-checked route/reader-to-endpoint mapping. Structural OpenAPI validity and nonempty source pointers alone are not evidence that this audit is complete.
