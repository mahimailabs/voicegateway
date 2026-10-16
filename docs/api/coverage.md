# Console read coverage

Issue #321 defines the read contract. The current console still imports captures and browser state directly; #322 and #323 will replace those imports with generated types and contract-backed mocks. This inventory describes the required mapping, not an implemented Go API. Phase 1 and the broader Connect outcome remain incomplete.

All console routes below have `/org/<org>/project/<project>` in front of them. Read operations use the same authorised project boundary. An organisation member must be authorised on the named project before any records are read. Browser login/session transport remains a separate decision, as agreed with the maintainer on 15 October 2026. Project keys and short-lived widget tokens are separate credentials, not a substitute for an organisation member's console permissions.

## Pages and their inputs

| Console surface | Required read resources | Current evidence and derivation |
| --- | --- | --- |
| `/` | Same as Overview | Redirect in `dashboard/src/router.tsx`; no independent data source. |
| `/overview`, `/settings` background | Calls, media-server watch, Cloud agents, worker samples, SIP, egress, ingress, provider probes, thresholds | `features/overview/overview.tsx`, `wire-data.ts`, `latency-chain.tsx`, `features/profile/provider-globe.tsx`. Fleet, verdict, stage medians, issue counts and charts are client computations over these inputs. |
| `/calls` | Calls, media-server watch, thresholds, call tags and archive state | `features/calls/calls-page.tsx`, `lib/api.ts`, `call-archive.ts`, `features/call/call-tags.tsx`. Filters, channel classification, findings and medians are computed in the console. |
| `/calls/$room` | Call detail, recording metadata/audio, media-server watch, thresholds, call tags and archive state | Same Calls page opens `features/call/call-view.tsx`. Its transcript, event log, spans, tool details and usage use the Call payload. Room-name lookup is a preview limitation; the API must identify one conversation, not merge reused rooms (decision 0012). |
| `/project` | Project snapshot | `features/project/project-page.tsx` reads `project-snapshot.json`, including an explicitly opt-in RemoteSession observation. The snapshot is distinct from the multi-window room watch. |
| `/agents` | Cloud agents, media-server watch, capacity result, thresholds | `features/agents/agents-model.ts`, `agent-drawer.tsx`, `capacity.tsx`. Jobs, joins and concurrency derive from participant/dispatch observations. Idle self-hosted workers are not discoverable through CloudAgent; an absent row is not proof of no worker. |
| `/sfu` | Media-server watch | `features/media-server/media-server-page.tsx`, `room-drawer.tsx`. Rooms, tracks, permissions, dispatch/job state history, SIP participant attributes and watch windows remain available. A last observation is not a room-end event. |
| `/sip` | SIP configuration, media-server watch, authorised phone-number projection | `features/sip/sip-page.tsx`, `sip-model.ts`. Paths, provider hints and checks derive from trunks/rules and SIP participants. Full numbers currently come from an optional ignored local file; production must use the authenticated read boundary, never that public file. |
| `/egress` | Egress snapshot and observed rejected requests | `features/egress/egress-page.tsx`. Status histories are sampled observations, not complete webhook history. Rejections are only those actually captured by VoiceGateway. Storage credentials are excluded. |
| `/ingress` | Ingress snapshot | `features/ingress/ingress-page.tsx`. Published tracks and current/last-session media are retained. No stream key is returned. |
| `/issues` | Calls, media-server watch, Cloud agents, SIP, thresholds, issue disposition | `features/issues/issues-model.ts`, `issue-state.ts`, call/agent/SIP check helpers. Issue grouping, severity and regression are computed; there is no independent measurement called an issue. |
| `/alerts` | Calls, media-server watch, Cloud agents, SIP, egress, ingress, thresholds, rule setup | `features/alerts/alerts-model.ts`. Seven rules show what would have fired; this is not delivery history. Delivery retries/deduplication/recovery belong to the alert-behaviour workstream. |
| `/live` | Media-server watch, calls | `features/live/live-page.tsx`. Current UI replays captured rooms, shortens idle gaps and repeats. The read contract provides observations, not an invented live stream or a transport decision for Phase 2. |
| `/embed/$widget` | The same inputs used by the selected widget | `features/embed/embed-page.tsx`, `widgets.ts`: answer-time and turn-anatomy use Overview data; calls uses Calls; call uses call detail; issues uses issue inputs. Branding/theme are UI data. Project/widget restrictions and allowed framing origins come from decision 0023. |
| App shell, switchers, command palette | Authorised workspaces and the page resources above | `components/workspace-switcher.tsx` is a placeholder organisation/project list. Palette call/agent/issue results reuse the same inputs, rather than a second search index. |

## Settings and local state

| Surface | Current source | Read-contract treatment |
| --- | --- | --- |
| Thresholds | `lib/thresholds.ts`, `vg.thresholds` | Project settings with positive millisecond budgets. The defaults (1500, 2000, 1000 ms) are configuration, never observations. |
| Tags and archived calls | `features/call/call-tags.tsx`, `features/calls/call-archive.ts` | Read metadata keyed by canonical call identity. Current room-name keys cannot safely identify reused-room conversations. Mutation and migration implementation are outside #321. |
| Resolved/ignored issues | `features/issues/issue-state.ts`, `vg.issue-state` | Read disposition and decision time; absence means open. Regressed is derived from the latest known hit compared with the disposition time. Unknown hit time remains null. |
| Alert enablement and destinations | `features/alerts/alerts-model.ts`, `vg.alert-rules` | Read rule setup, not delivery history. Channel names are enums; notification webhook URLs and keys never enter the read response. |
| Account | `features/settings/settings-dialog.tsx` | Name/email currently say Not set. The hard-coded Admin label is a preview, not evidence of an authenticated role. Actual membership must follow decision 0019's Admin/Member/Viewer rules. |
| Organisation / People | `components/workspace-switcher.tsx`; `vg.invites` | Authorised workspace list and pending invitation records. The current local list is not proof that mail was sent or membership granted. |
| API keys / MCP | `features/settings/settings-dialog.tsx` | Disabled Create key and Not set MCP URL are placeholders. They do not justify inventing issued keys or a running MCP server. No secret values in read models. |
| Data | Calls and `settings-dialog.tsx` | Recording count derives from calls. Bucket and retention fields are not configured in captures and stay null; no guessed retention policy. |
| Integrations / Embeds | `settings-dialog.tsx`, `widgets.ts` | Notification connection and allowed-origin settings are unavailable until configured on the server. Static provider names, widget descriptions and client command templates need no data endpoint. |
| Appearance, time zone, shortcuts, URL state | `components/theme.tsx`, browser APIs, `lib/url-state.ts` | Client presentation state; excluded from server telemetry. Theme remains browser-local. |
| Enterprise rows / audit | `settings-dialog.tsx` | Locked explanatory UI from decisions 0017–0019, not fabricated audit records or Enterprise implementation. |

## Captured source families

| Files under `dashboard/src/mocks/` | Actual source and limits |
| --- | --- |
| `calls.json` | Local LiveKit 1.13.3 webhooks and Agents 1.5.7 OTLP traces; long attributes were truncated by the spike receiver. |
| `capture-session.json`, `capture-cloud.json`, `capture-crash.json`, `capture-sip.json` | Agents observability upload: OTLP, chat history and stereo session audio. Scripted callers/synthetic caller speech are labelled in each fixture's source note; agent behaviour is captured. Crash fixture begins at the replacement session, not the failed process's first join. |
| `console-session.json`, `console-realtime.json` | RemoteSession events from the Agent Console. Realtime has no per-message stage latency; neither fixture has recorded audio. |
| `media-server.json` | RoomService, participant/track reads, AgentDispatchService and connection ServerInfo across four sampled watch windows. Earlier watch retained no tracks/permissions/dispatch detail. |
| `agents.json` | LiveKit CloudAgent ListAgents/ListAgentVersions; only Cloud deployments. Resource quantities retain their API units. |
| `workers.json` | Prometheus `lk_agents_worker_load` and `lk_agents_active_job_count`; both workers shared one host. Load is not per-worker CPU utilisation. |
| `sip.json` | ListSIPInboundTrunk/ListSIPOutboundTrunk/ListSIPDispatchRule/ListPhoneNumbers. Numbers are masked; auth is presence only. |
| `sip-test.json` | Explicitly hand-written layout test, not captured traffic. May validate schema shape but cannot establish measurement provenance or populate production results. |
| `egress.json`, `ingress.json` | Two-second ListEgress/ListIngress samples from labelled test operations. Ingress/egress history is partial. Credentials were omitted. |
| `project-snapshot.json` | One read-only project snapshot plus a separately noted hidden-participant RemoteSession observation. Do not imply observation is on by default. |
| `capacity.json` | Capacity feasibility run on a shared ten-core development Mac; limited by the tester. Not a production per-replica capacity guarantee. |
| `network-probe.json` | Captured development-host TCP/TLS/first-byte probes and approximate edge geolocation. Not model inference location or measured per-call provider latency. The contract reads recorded observations; it does not make the product call model providers. |

## Non-API assets

`/globe/countries.geojson` is static map geometry (`components/network-globe.tsx`). `/preview/recording.json` and its WAV are guarded by `import.meta.env.DEV`, generated locally, ignored and explicitly synthetic (`features/calls/calls-page.tsx`). They are not production calls or API endpoints. `/captures/sip-numbers.json` is optional local PII enrichment; its production replacement must be authenticated and cannot be a public static asset.

## Contract invariants to validate

- A canonical call identifies a project-scoped conversation with a room/time window and optional job/trace joins. A room name or room SID alone is insufficient. Call metadata uses the same identity.
- Wall-clock times have a stated UTC epoch or RFC 3339 representation. Relative event, span, message and recording clocks have a stated origin; offsets from different clocks cannot be blindly added.
- Every property names evidence and origin through `x-source`; each numeric telemetry field declares its unit and missing-value meaning. Observed zero is valid; missing never becomes zero.
- Null collections mean not observed/unavailable; empty collections mean an observed empty result, subject to the accompanying coverage window. Sampling gaps and stale reads stay visible.
- Paging is applied consistently. Client aggregates must consume every page of their selected snapshot/range; they must never treat the first page as a full-project total.
- Failures are typed responses. Source unavailability is distinguishable from an authorised empty project. Keys, TURN passwords, ingress stream keys, notification secrets and storage credentials are excluded.

## Validation and audio reads

Install the locked tools with `npm --prefix dashboard ci`. Run `make api-check` for pinned Redocly OpenAPI validation plus source/reader/route checks, and `make test` for validator failure-path regressions and existing threshold tests. Dashboard CI runs both, then `make lint` (including TypeScript) and the production build. `x-source` records the source kind, repository evidence file, exact JSON pointer for captures, and derivation/availability detail. A null pointer is reserved for computed/configuration rules evidenced in code or these documents. Structural source coverage is not a substitute for reviewing those rules against native LiveKit definitions.

Recording metadata names a project-scoped media resource through `getRecordingMedia`. Call and watched-room recording shapes carry the same opaque recording id. Their captured `/captures` URLs are fixture evidence, not a production public-file design. The read endpoint defines normal/private and partial byte responses for browser playback; adapters should use standard HTTP range-serving facilities. Missing/expired retained media is 404, source storage failure is 503, and unsatisfiable byte range is 416. Never fetch an arbitrary user-supplied storage URL or treat possession of an id as authorisation. Waveform/segment metadata remains separate from the audio bytes; empty captured arrays do not prove silence or alignment.
