# Console read coverage

Issue #321 defines the read contract. The console reads every page through `dashboard/src/lib/api.ts` (TanStack Query over the generated types), and the MSW mock under `dashboard/src/mocks/` answers the contract's paths from the captured fixtures (#322, #323). Outside `dashboard/src/mocks/` nothing computes a server read from fixtures, and the only imports from it are `main.tsx` starting the mock and the two design previews listed under Non-API assets. This inventory describes the required mapping, not an implemented Go API. Phase 1 and the broader Connect outcome remain incomplete.

All console routes below have `/org/<org>/project/<project>` in front of them. Read operations use the same authorised project boundary. An organisation member must be authorised on the named project before any records are read. Browser login/session transport remains a separate decision, as agreed with the maintainer on 15 October 2026. Project keys and short-lived widget tokens are separate credentials, not a substitute for an organisation member's console permissions.

## Pages and their inputs

| Console surface | Required read resources | Current evidence and derivation |
| --- | --- | --- |
| `/` | Same as Overview | Redirect in `dashboard/src/router.tsx`; no independent data source. |
| `/overview`, `/settings` background | Call stats, issues, calls, media-server watch, Cloud agents, worker samples, SIP, egress, ingress, provider probes, thresholds | `features/overview/overview.tsx`, `wire-data.ts`, `latency-chain.tsx`, `features/profile/provider-globe.tsx` (drawn from `getProviderProbes`). The figures, the Every turn strip, Turn anatomy and the agent cards read `getCallStats` (each turn and call names its agent); the fleet, rooms, SIP, Egress, Ingress, worker samples and provider probes read their own endpoints. The Live wire reads `byHour` and each agent's `providers`, and the stage chain reads `chain`. |
| `/calls` | Calls, media-server watch, thresholds, call tags and archive state | `features/calls/calls-page.tsx`, `lib/api.ts`, `call-archive.ts`, `features/call/call-tags.tsx`. The range, filters, sort and paging run on the server through `listCalls`; the stat cards read `CallPage.totals`, computed over every matching call. `getCallStats` serves Overview only. |
| `/calls/$callId` | Call detail, recording metadata/audio, media-server watch, thresholds, call tags and archive state | Same Calls page opens `features/call/call-view.tsx`, read with `getCall` by the conversation id in the URL, so a reused room never opens the wrong call (decision 0012). Its transcript, event log, spans, tool details and usage use the Call payload; the turn waterfall's network hops read `getProviderProbes`, and an embedded call, which may not read it, draws none. |
| `/project` | Project snapshot | `features/project/project-page.tsx` reads `getProjectSnapshot`, served from `project-snapshot.json`, including an explicitly opt-in RemoteSession observation. The snapshot is distinct from the multi-window room watch. |
| `/agents` | Cloud agents, media-server watch, capacity result, thresholds | `features/agents/agents-model.ts` (`agentsFrom` over `getCloudAgents` and `getMediaServer`), `agent-drawer.tsx`, `capacity.tsx`. Jobs, joins and concurrency derive from participant/dispatch observations. Idle self-hosted workers are not discoverable through CloudAgent; an absent row is not proof of no worker. |
| `/sfu` | Media-server watch | `features/media-server/media-server-page.tsx`, `room-drawer.tsx`. Rooms, tracks, permissions, dispatch/job state history, SIP participant attributes and watch windows remain available. A last observation is not a room-end event. |
| `/sip` | SIP configuration, media-server watch, authorised phone-number projection | `features/sip/sip-page.tsx`, `sip-model.ts`. Paths, provider hints and checks derive from trunks/rules and SIP participants. Full numbers currently come from an optional ignored local file; production must use the authenticated read boundary, never that public file. |
| `/egress` | Egress snapshot and observed rejected requests | `features/egress/egress-page.tsx`. Status histories are sampled observations, not complete webhook history. Rejections are only those actually captured by VoiceGateway. Storage credentials are excluded. |
| `/ingress` | Ingress snapshot | `features/ingress/ingress-page.tsx`. Published tracks and current/last-session media are retained. No stream key is returned. |
| `/issues` | Issues, media-server watch, Cloud agents, SIP, thresholds, issue disposition | `features/issues/issues-model.ts` reads `listIssues`; `issue-state.ts` writes `updateIssueMetadata`; `listIssues` includes metadata and evidence revisions from the same database snapshot. The mock computes the groups (`mocks/insights.ts`) with the same call, agent and SIP checks the other pages run. Issue grouping, severity and regression are computed; there is no independent measurement called an issue. Each issue has a `kind`: call findings and recorded egress or ingress failures (source `delivery`) are `event` (hits dated by the call or the failure, kept when `from <= at < to`), Agent workers and Phone bridge checks are `standing` (true of the latest read, undated hits, listed in every range and shown as Current, Stale, Source unavailable or Not confirmed by the evidence state of the latest source read). The headline and `getCallStats` `issueCounts` count only event issues. |
| `/alerts` | Alert events, media-server watch, Cloud agents, SIP, egress, ingress, thresholds, rule setup | `features/alerts/alerts-model.ts` reads `listAlertEvents` and `getAlertSetup`; the mock evaluates the rules in `mocks/insights.ts`. Seven rules show what would have fired; this is not delivery history. Delivery retries/deduplication/recovery belong to the alert-behaviour workstream. |
| `/live` | Media-server watch, calls | `features/live/live-page.tsx`. Current UI replays captured rooms, shortens idle gaps and repeats. The read contract provides observations, not an invented live stream or a transport decision for Phase 2. |
| `/embed/$widget` | The same inputs used by the selected widget | `features/embed/embed-page.tsx`, `widgets.ts`: answer-time, calls and time-per-turn use getCallStats; issues uses listIssues; recent-calls uses listCalls; call uses call detail. Size, branding and theme are UI data. Project/widget restrictions and allowed framing origins come from decisions 0023 and 0034. |
| App shell, switchers, command palette | Authorised workspaces and the page resources above | `getWorkspaces`, read once at start (`lib/workspaces.ts`) before the router resolves a URL's project; the mock answers one organisation and project (`mocks/workspaces.ts`). Palette call/agent/issue results reuse the same inputs, rather than a second search index. |

## Settings and local state

Every settings read below goes through the contract. The console's saves stay in this browser (`lib/local-store.ts`) until web stores them, and the mock API serves them back from the same storage, validated as the server will validate its rows.

| Surface | Current source | Read-contract treatment |
| --- | --- | --- |
| Thresholds | `getProjectSettings`; saved to `vg.thresholds` (`lib/local-store.ts`) | Project settings with positive millisecond budgets. The defaults (1500, 2000, 1000 ms) are configuration, never observations. |
| Tags and archived calls | `features/call/call-tags.tsx`, `features/calls/call-archive.ts` | Read metadata keyed by canonical call identity. Current room-name keys cannot safely identify reused-room conversations. Mutation and migration implementation are outside #321. |
| Resolved/ignored issues | `listIssues`; saved through `updateIssueMetadata` (Postgres) | Read state, decision time and evidence revisions; no saved decision means open. A resolved issue regresses when a newly observed occurrence advances `evidenceRevision` past `reviewedEvidenceRevision`, independently of hit time. Tag and archive edits never acknowledge new evidence. |
| Alert enablement and destinations | `getAlertSetup`; saved to `vg.alert-rules` | Read rule setup, not delivery history. Channel names are enums; notification webhook URLs and keys never enter the read response. |
| Account | `features/settings/settings-dialog.tsx` | Name/email currently say Not set. The hard-coded Admin label is a preview, not evidence of an authenticated role. Actual membership must follow decision 0019's Admin/Member/Viewer rules. |
| Organisation / People | `getWorkspaces`; `getInvitations`, saved to `vg.invites` | Authorised workspace list and pending invitation records. The current local list is not proof that mail was sent or membership granted. |
| API keys / MCP | `features/settings/settings-dialog.tsx` | Disabled Create key and Not set MCP URL are placeholders. They do not justify inventing issued keys or a running MCP server. No secret values in read models. |
| Data | `listCalls` totals and `getProjectSettings` | Recording count is `CallPage.totals.recorded` over `callCount`; a null total shows as unknown. Bucket and retention fields are not configured and the read returns null; no guessed retention policy. |
| Integrations / Embeds | `settings-dialog.tsx`, `widgets.ts` | Notification connections are unavailable until configured on the server; allowed origins come from `getProjectSettings` and are null until configured. Static provider names, widget descriptions and client command templates need no data endpoint. |
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
| `capacity.json` | Capacity feasibility run on a shared ten-core development Mac; limited by the tester. Not a production per-replica capacity guarantee. The capture recorded one time and no per-step times, CPU, failed, late or no-answer counts: the mock gives every time that one time and leaves the rest null. A run started in the mock replays these steps, one per read. |
| `network-probe.json` | Captured development-host TCP/TLS/first-byte probes and approximate edge geolocation. Not model inference location or measured per-call provider latency. The contract reads recorded observations; it does not make the product call model providers. |

## Non-API assets

`/globe/countries.geojson` is static map geometry (`components/network-globe.tsx`). `/preview/recording.json` and its WAV are guarded by `import.meta.env.DEV`, generated locally, ignored and explicitly synthetic (`features/calls/calls-page.tsx`). They are not production calls or API endpoints. The Calls page imports `mocks/preview.ts` for that preview, and the SIP page imports `sip-test.json`, a hand-written layout test shown only with `?data=test`; neither is a contract read. `/captures/sip-numbers.json` is optional local PII enrichment; its production replacement must be authenticated and cannot be a public static asset.

## Source clocks and alignment

The maintainer selected preserved source clocks on 2026-10-16. `Call.clocks` names each distinct timeline; each span, turn, event and recording carries a required `clockId` that resolves to exactly one call-local entry. IDs are unique within the call, including when multiple traces or replacement agent sessions are joined. Recording segments inherit their recording's clock. The `x-clock` extension binds each relative position to its clock reference; durations and delays are intervals, not positions.

A clock records what its zero means (`call_start`, `session_start`, `recording_start`, `first_event`, `trace_start` or `unknown`). `originAt` is an observed UTC anchor or null. `offsetToCallStartSeconds` is signed: **call-relative seconds = source-relative seconds + offset**; divide span milliseconds by 1000 first. Its `alignment` is `observed`, `inferred` or `unknown`. Unknown alignment requires a null offset; observed/inferred alignment requires a numeric offset. Even zero requires evidence. Inferred alignment must be labelled and its derivation documented by the adapter; it must never become an observed UTC anchor.

Source-relative positions remain useful for ordering within the same clock when alignment is unknown. Do not overlay them with recordings, spans or other event timelines until a mapping is known. An observed UTC anchor alone does not establish a call-relative mapping if the call-start evidence is uncertain. Implementations must validate clock ID uniqueness/resolution and recording-start origins; JSON Schema cannot express those cross-record relationships.

- `capture-session`, `capture-cloud`, `capture-sip` and `capture-crash` explicitly declare recording-relative times. Preserve that origin, without assuming that `Call.startedAt` equals recording start. The crash capture begins after the replacement agent joins, not at the first failed process.
- `console-session` and `console-realtime` declare seconds from the first event. Preserve `first_event`; these captures have no recording anchor.
- `calls.json` retains span offsets without an explicit origin/alignment anchor. Use `unknown` and null alignment rather than inventing trace-start or call-start alignment from a first span at zero.
- Recording segment positions are recording-relative only when measured alignment exists. Placeholder arrays do not establish either silence or alignment. Absolute room-watch timestamps retain their declared epoch units.

These rules are new API metadata derived from retained evidence; the existing preview fixtures do not already contain the metadata fields. Native LiveKit synchronisation helpers can use observed RTP/RTCP evidence in future adapters; they cannot reconstruct missing offline anchors. The clock examples in OpenAPI illustrate valid shapes, not newly captured measurements.

## Contract invariants to validate

- A canonical call identifies a project-scoped conversation with a room/time window and optional job/trace joins. A room name or room SID alone is insufficient. Call metadata uses the same identity.
- Wall-clock instants are RFC 3339 UTC strings (`format: date-time`), named with an `At` suffix or as a range bound; the evidence note names the native unit they were converted from. Epoch numbers are never returned. Relative event, span, message and recording clocks have a stated origin; offsets from different clocks cannot be blindly added.
- Every property names evidence and origin through `x-source`; each numeric telemetry field declares its unit and missing-value meaning. Observed zero is valid; missing never becomes zero.
- Null collections mean not observed/unavailable; empty collections mean an observed empty result, subject to the accompanying coverage window. Sampling gaps and stale reads stay visible.
- Paging is applied consistently. The console never totals, sorts or filters across pages itself. `listCalls` returns `CallSummary` rows filtered and sorted on the server (channel, highest finding, answer time, length, turns, unknown values, tags, recorded, archived), with unknown values sorting last and never matching a numeric bound. Full spans, turns and events come only from `getCall`.
- Page aggregates are server reads over a required range: `getCallStats` (Overview figures, agent rows, Turn anatomy, the answer-time, calls and time-per-turn widgets), `listIssues` (Issues, the issues widget, issue counts) and `listAlertEvents` (what each rule would have fired on). Each says whether the range is completely stored; when it is not, totals are null rather than an undercount.
- `getCallStats` from web (phase 04e) reads Postgres call summaries. Known limits: the server does not detect ingest gaps yet, so `complete` is true and means every call stored since the project was connected. Turn anatomy, the stage chain, providers, anatomy turns and issue counts are null (not computed yet), never empty or 0. `console` and `local` channel counts are null, since no stored source tells them apart. A day or hour bucket with no stored call is null before the project was connected or still to come; a partial bucket counts the calls so far. The first and last day or hour of a range that starts or ends mid-bucket count only the part inside the range. Follow-up: the read loads every call in the range and totals it in Go; long ranges on a busy project need per-figure SQL instead.
- Failures are typed responses. Source unavailability is distinguishable from an authorised empty project. Keys, TURN passwords, ingress stream keys, notification secrets and storage credentials are excluded.

## Validation and audio reads

Install the locked tools with `npm --prefix dashboard ci`. Run `make api-check` for pinned Redocly OpenAPI validation plus source/reader/route checks, and `make test` for validator failure-path regressions and the mock API's contract tests. Dashboard CI runs both, then `make lint` (including TypeScript) and the production build. `x-source` records the source kind, repository evidence file, exact JSON pointer for captures, and derivation/availability detail. A null pointer is reserved for computed/configuration rules evidenced in code or these documents. Structural source coverage is not a substitute for reviewing those rules against native LiveKit definitions.

Recording metadata names a project-scoped media resource through `getRecordingMedia`. Call and watched-room recording shapes carry the same opaque recording id. Their captured `/captures` URLs are fixture evidence, not a production public-file design: the mock returns a playback url on `getRecordingMedia` in the requested project, with the file name (the room SID) as the id, and serves the file only for an id a capture names (`mocks/recordings.ts`). The read endpoint defines normal/private and partial byte responses for browser playback; adapters should use standard HTTP range-serving facilities. Missing/expired retained media is 404, source storage failure is 503, and unsatisfiable byte range is 416. Never fetch an arbitrary user-supplied storage URL or treat possession of an id as authorisation. Waveform/segment metadata remains separate from the audio bytes; empty captured arrays do not prove silence or alignment.

## Credential boundaries

`x-access` is the mandatory authorisation policy for these console reads. The browser session mechanism is intentionally not chosen by #321; its absence from an OpenAPI security-scheme binding does not permit anonymous reads. Project keys remain separate hashed, project-scoped ingest/MCP credentials and are not implicitly organisation membership. Embed credentials follow decision 0023: short-lived, read-only, one project and an allowed widget set, with an origin allow-list. They must not grant arbitrary console/settings access or expose the project API key to an iframe. Token minting and the eventual wire/session binding belong to the server/access workstream.

`x-embed-access` marks the only reads an embed token may make, per widget: answer-time, calls and time-per-turn read `getCallStats`, issues reads `listIssues`, recent-calls reads `listCalls`, call reads `getCall`, and `getRecordingMedia` only for a token with `transcriptsAndAudio`. The gate enforces the same pairs (`embedAccess`, decision 0034). Each requires one project, an origin allow-list and read-only use. `surfaces.json` `embedReads` lists the same pairs, and `make api-check` fails when an embed token would reach a read its widget does not need (settings, SIP, invitations, workspaces) or a widget's read lacks embed access. Every other read stays organisation-member only.

## Persisted issue evaluation coverage

IssueList.coverage is read in the same Postgres snapshot as its occurrences and metadata. Pending
call counts include retry backoff, stale summaries and changed project thresholds, restricted to
retained servable calls in the selected range. Unavailable calls were evaluated but lacked source
evidence. Pending job counts cover project observations awaiting evaluation or exact call/participant
correlation across retained history. Tag, archive and search filters do not hide these gaps.

Component sources report unknown (no evaluation), unavailable (failed read), stale, partial (missing
rule evidence), or current (fresh and fully evaluated). Current does not mean healthy: findings are
reported separately. The complete flag requires zero pending/unavailable evaluations and current
coverage for every expected source. It does not guarantee complete upstream capture, historical
retention before collection, or the absence of unreported failures. Numeric counts may be null when
unavailable and must never be displayed as zero in that case.
