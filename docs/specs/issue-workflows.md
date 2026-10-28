# Persistent issue workflows

## Current evidence

Done. Issues reads persisted call, standing, job, recording and ingress findings with scoped
decisions, tags and archive (#435, #436, #440). Call tags and archive persist per immutable call ID
(Postgres 00016); issue evidence and decisions persist per stable issue and source identity
(Postgres 00017). The full root race, lint, contract, frontend and browser gates passed on main
aecb612 on 2026-10-28 (see docs/progress.md). Webhook alert delivery is separate work.

## Required end state

1. Read real findings from recorded call/turn evidence and latest supported component samples.
   Reuse the same rule definitions for subsequent webhook alerts. Distinguish event time from
   standing state, unknown from healthy, and a missing source from recovery.
2. Persist issue metadata by organisation, project and stable issue/rule identity. Persist call
   metadata by organisation, project and immutable call UUID, never display room name.
3. Viewer and scoped API keys read only. Member/Admin mutations go through the existing role gate
   and same-origin protection. Reject wrong scope and missing identities without leaking data.
4. Tags are bounded, normalized, unique values. Archive is reversible and independent of resolving
   or ignoring an issue. Resolving stores the latest occurrence observed, so a later occurrence can
   regress; an archive must never falsify underlying evidence or suppress alert evaluation silently.
5. Writes compare revisions atomically. Stale saves and Undo fail with 409. Bulk actions either
   commit as one transaction or explicitly report per-item outcomes; do not show partial success as
   complete success. Refresh/cancel old scope and denied data using the existing query conventions.
6. Search, tags and archive filter actual persisted records and totals. Cursor/snapshot semantics
   must remain consistent when metadata changes. Evidence links resolve to the permitted real call.
7. Preserve the approved table, filters, drawer and menus. Replace local writes with real API writes;
   show pending/error/permission states. No browser-storage migration or fallback fake success.

## Acceptance evidence required

- Native Postgres: persistence/reopen, same room name in different calls/projects, isolation,
  concurrent revisions, rejected stale writes, reversible archives and tag normalization.
- HTTP: roles, project keys, cross-origin, malformed/oversized requests, wrong project and missing ID.
- Real call evidence: findings agree with source durations, missing measurements remain unknown,
  standing source failures do not become healthy, and regression follows new evidence.
- Queries: search/tag/archive results and counts agree; snapshot changes cannot silently duplicate or
  skip rows; list/detail metadata agrees.
- Browser: another session sees saved state, reload preserves it, failure preserves the user's intent,
  Viewer has no mutation controls, project switching cannot retain or mutate prior-project metadata.
- Full root race/test/lint/schema/provenance/browser gates, focused PR review and merge.

Settings and alerts dependencies remain explicit. Completing metadata alone does not complete the
issue-workflow task, and completed issue persistence does not prove webhook delivery/retries.

## Implementation progress

Call metadata is implemented through scoped GET/PUT APIs and a scoped tag catalogue. Member/Admin
writes compare revisions atomically; Viewer and project keys read only. Stale saves and Undo fail
with 409. Tags/archive are independent of worker retries and reused room names. List/detail reads
agree, and project metadata revisions invalidate pagination after edits. The approved UI waits
for confirmed saves, reports partial bulk failures, and clears denied/deleted data across query
observers. Project changes prevent retained actions from mutating the previous project.

Validation on PR #435's implementation head 23e97377: full Docker-backed Go race suite, Go lint and
SQL generation checks, API schema/provenance/generated-type checks, 225 frontend tests, TypeScript
and all 84 browser/accessibility checks passed. Browser tests cover HTTP tag/archive writes and
reload in desktop/mobile light/dark. Native tests cover independent web instances, scope,
permissions, cross-origin refusal, malformed input, concurrent revisions, stale Undo, filters and
snapshot invalidation. The CLI-key integration fixture now includes the metadata dependency,
matching production wiring. Automated review found stale documentation, corrected here.

Issue findings/dispositions and webhook delivery remain unfinished. Completing call metadata does
not complete the issue-workflow roadmap task, which remains unchecked.

## Deployment

Migration 00016 takes schema locks while adding checked columns and building call metadata indexes.
For large installations, schedule a maintenance window and stop the web and worker processes before
running migrations, then start them after migration success. This migration is transactional; it
does not claim a zero-downtime upgrade. Concurrent index construction alone would not remove the
column/constraint locking, so the atomic migration is retained with this explicit operating rule.

The tag catalogue scans servable calls within one project to collect distinct tags. Its query does
not use the tag GIN index. Observe its query duration as projects grow; the index supports membership
filters, not catalogue aggregation.

The next implementation adds native call finding evaluation and a project-scoped occurrence store.
Worker summary writes and findings share one transaction. Replayed evidence does not create a new
occurrence; a newly observed occurrence regresses a resolved issue even when its event timestamp is
older. Tags and archive edits never acknowledge unseen evidence. Per-rule coverage prevents missing
metrics from clearing previous findings. The issue metadata API compares both metadata and evidence
revisions, permits Member/Admin writes and Viewer/project-key reads, and rejects cross-origin writes.
Native storage, worker, authenticated HTTP and role/contract checks pass under the race detector. Go lint, SQL generation, schema/source attribution and frontend type checks pass. Issue listing, standing source evaluation,
UI integration, historical backfill and webhook delivery are still unfinished. Do not check off the
roadmap task based on this foundation.

Issue list integration now reads persisted occurrences and their decision revisions in one
repeatable-read snapshot. Literal search, normalized tags, archive state and source range filter
those records. The list includes canonical call IDs for evidence links. It explicitly reports
incomplete coverage until standing-source evaluation and historical backfill are implemented.
The approved table now saves resolve/ignore/reopen, tags, archive/restore and revision-safe Undo
through the API. Bulk actions report individual failures, Viewer controls are read-only, and
retained actions cannot edit a different project after navigation. Only the explicit development
adapter uses browser storage. List storage tests, 232 frontend tests and 88 browser/accessibility checks pass, including scoped
resolve/archive writes and reload in desktop/mobile light/dark. The obsolete browser-local
disposition API was removed. Authenticated list verification and native historical backfill checks pass under the race detector.
Historical calls are evaluated without rewriting their summaries. Settings revisions requeue call
findings; monotonic evaluation revisions reject stale attempts, and source failures use bounded
persisted backoff. A native Redis/ClickHouse/Postgres test verifies failure, retry, deduplication
and unchanged summaries. Go lint and SQL generation checks pass. The full native run exposed a first-batch assumption in the new backfill test. It now
exercises a backlog larger than one sweep; targeted native checks pass. A clean full final run
is still required before merge.
Standing findings and webhook delivery remain open; the roadmap task stays unchecked.

Standing-source implementation is in progress. SIP polling now evaluates native redacted trunk
and dispatch projections and persists findings with source read health. Per-component rule
coverage preserves earlier findings when a successful read omits an individual field. Failed
reads preserve evidence; newer explicit healthy reads or confirmed component deletion clear it.
Out-of-order reads cannot replace newer observations, and unchanged readings do not regress a
resolved decision. Each positive finding has its own last-confirmed source timestamp. The API and
UI distinguish current, stale, unavailable and unconfirmed standing evidence; event hits remain
recorded evidence. Storage lifecycle and SIP rule tests pass; final integration gates are pending.

SIP evidence audit (2026-10-20): LiveKit Docs MCP secure-trunking and inbound-trunk pages, plus
protocol v1.52.1 SIPTransport/SIPMediaEncryption and the existing projectSIP projection. Explicit
UDP/TCP is distinguished from AUTO, which does not establish negotiated transport. Trunk media
configuration is not proof of negotiated media or per-call overrides. Redacted phone suffixes
cannot establish duplicate full numbers, so that preview heuristic is not promoted to a native
finding. The worker uses existing SDK clients and projections, without additional provider calls.

Sources: https://docs.livekit.io/telephony/features/secure-trunking/ and
https://docs.livekit.io/telephony/accepting-calls/inbound-trunk/.

Cloud deployment evaluation is wired to the existing Cloud agent poller. Explicit failure status
strings produce a finding per agent; unknown/new status strings do not clear previous evidence.
Sleeping is a reported non-failure state, not proof of failed dispatch or a reason to recommend
paid hosting. The native protocol exposes status as a string, so the evaluator keeps unknown
values unknown. Job outcomes and join latency still need their own source evaluation; deployment
status is not substituted for either. Source: protocol v1.52.1 AgentDeployment.status and
https://docs.livekit.io/deploy/agents/quickstart/.

Standing checkpoint validation: targeted native race tests (source lifecycle, list freshness,
SIP/Cloud rule evaluation), Go lint, SQL generation, API schema/provenance, TypeScript, all 233
frontend tests and 88 browser/accessibility checks pass. This does not complete the broader issue
workflow: job failures/join latency, call rule parity and overall source coverage still need work,
followed by a clean full native run and PR review. No roadmap checkbox is advanced.

Join-source audit: call_participants already stores the native participant identity and join time
(internal/join/webhook.go). Job correlation must use that identity and the job ID within the same
project and immutable call, not an agent-name-only match across jobs. The media projection alone
omits participant identity; using its first participant would risk attributing the wrong join.

Job evidence checkpoint: the poller now passes native job observations to Postgres before emitting
its live-change notification. The native participant identity is retained privately for exact
correlation and excluded from JSON projections. Repeated polls do not advance the observation
revision without a semantic change. Missing fields, failed reads and room closure preserve earlier
failure evidence. The Postgres race regression covers these cases and scope rejection. The
notification-order and private-identity regressions also pass with the race detector. Issue evaluation and late-webhook correlation
remain unfinished; this checkpoint does not complete issue workflows.

Job evaluation now runs in bounded worker sweeps. Native failed jobs produce event findings;
without a reported failure timestamp, the detail labels the timestamp as first observation.
Dispatch-to-join findings require a reported participant identity matched to an agent participant
in the same project and room instance. A late webhook can attach the canonical call link and
requeue evaluation. Missing or negative intervals do not establish healthy join latency. Project
threshold revisions requeue evaluations; observation and evaluation revisions reject stale writes.
Call finding replacement is constrained to its own source and cannot erase job evidence.

Targeted root race checks pass for exact and cross-project correlation, late call/participant
arrival, threshold effects, stale evaluation rejection, persistence through room closure, private
identity projection and notification ordering. Final full-suite checks and review remain pending.

Call-diagnosis rule audit: exact model finish-reason matching now agrees with persisted findings
(including rejecting no_error and malformed/non-string reason arrays). Endpointing evidence no
longer claims the configured delay caused the observation; backchannel-to-turn matching is labeled
as a timing-based interpretation. Endpointing evidence no longer hides a measured slow-response
finding. These local interpretations are not promoted to persisted issues without equivalent
source correlation. All 243 frontend tests and dashboard lint/TypeScript checks pass for this audit.
The outstanding work remains source coverage reporting, final native/browser gates and review.

Coverage reporting now reads evaluation counts and source state in the same Postgres snapshot as
issue occurrences. Pending calls include retry backoff and changed settings; unavailable calls
have completed evaluation without retained source evidence. Pending job counts include late
participant/call correlation and settings changes. Every expected component source is explicit;
missing, failed, stale and partial reads remain distinguishable. SIP and Cloud evaluations mark
missing rule evidence as partial. The existing Issues status line exposes these gaps.

The complete flag describes evaluation of retained data and fresh component evidence, not a
claim that every upstream call was captured or no unreported failure exists. Sources without an
implemented native evaluation remain unknown, keeping the aggregate incomplete. Contract lint,
source attribution and TypeScript validation pass; native coverage race tests and all 244 frontend tests pass
for this checkpoint. No roadmap completion checkbox changes.

Media-server coverage is now persisted atomically with job observations. Failed reads retain
prior job evidence; older reads cannot replace newer source health. Missing dispatch data,
participant identities or dispatch timestamps produce partial coverage. A successful self-hosted
LiveKit poll remains partial for job failure detection because ListDispatch does not reliably
report failures there under the verified V1 capability contract. Positive native failures are
still stored wherever observed. Native race regressions pass for partial, unavailable, recovered
and out-of-order source reads, including that self-hosted capability boundary.


### Recording and ingress evidence audit (2026-10-20)

The sink already invokes Joiner.Stored after ClickHouse insertion and before Redis
acknowledgement, including redeliveries. This is the durable processing boundary for
new webhook findings. Issue persistence there must be idempotent by scoped webhook
identity and must retain component findings even when no call can be correlated.
Historical retained events also need evaluation; wiring only new ingestion is insufficient.

The existing EgressWebhooks and IngressWebhooks history readers filter by supplied
component IDs and return status/timestamps without webhook event IDs. They cannot
serve as an exhaustive issue backfill or the event-ID deduplication contract unchanged.
Recording findings must preserve FAILED/ABORTED versus LIMIT_REACHED (cut short),
and absence from a later poll must not resolve a terminal recording occurrence.

Upstream telemetry/events.go at LiveKit commit
7c73bcc0c7045ece57948da1581d239cc6b33954 forwards IngressInfo in IngressEnded
and EgressInfo in EgressEnded. Source:
https://github.com/livekit/livekit/blob/7c73bcc0c7045ece57948da1581d239cc6b33954/pkg/telemetry/events.go
This proves payload forwarding, not that a real ingress failure emits ENDPOINT_ERROR.
The native failure verification required by alerts-behaviour.md remains outstanding.
No recording/ingress rule or roadmap completion is claimed by this audit.


Recording terminal events are now evaluated in Joiner.Stored after durable ClickHouse
insertion and before Redis acknowledgement. Native protobuf decoding accepts only
EGRESS_FAILED, EGRESS_ABORTED and EGRESS_LIMIT_REACHED on egress_ended. Postgres
stores scoped webhook occurrences, including events without a call, rejects cross-project
call links, and deduplicates replay. Call-finding refresh cannot clear recording evidence.
Descriptions retain status and egress identity, never raw provider errors or output URLs.
The native Redis/ClickHouse/Postgres ingestion test and Postgres replay/scope/late-link
regressions pass under the race detector. Historical recording backfill and ingress
failure evaluation remain unfinished. Full native race checks passed on baseline
117b7cd1 before this recording change; final full checks still apply to the final PR head.


Historical recording evaluation now uses a per-project Postgres cursor and batches of
100 retained egress_ended events. The cursor includes the exact ClickHouse nanosecond
received timestamp and dedupe key. It advances only after every finding in the page is
persisted, with revision checks against competing workers. Failed reads leave the cursor
unchanged and use persisted exponential retry delays bounded at one hour. Restart resumes
from the stored cursor. New events continue through the sink's pre-acknowledgement path.
The native 101-event same-timestamp boundary test and failed-read recovery test pass.
Ingress evaluation and source completeness integration remain unfinished; this does not
advance the roadmap checkbox.
Native recovery checks also pass for replay after findings commit but before checkpoint,
and for a retained event after its linked call is deleted: the finding remains unlinked
and no call is recreated. Go lint, format and SQL generation consistency checks pass.


The event evaluator and historical cursor now cover both recording and ingress findings.
Native signed-webhook verification against livekit-server v1.13.3 confirms ingress
ERROR and subsequent PUBLISHING produce the expected ended/started events. Only
an ingress_ended payload with an explicit ENDPOINT_ERROR creates an ingress finding.
Missing state, normal completion and recovery do not create extra failures or erase
historical failure evidence. Raw ingress error text is excluded from issue descriptions.
The bounded backfill regression now mixes 101 ingress/egress events at one exact
nanosecond timestamp and verifies restart/replay. Native ingestion tests cover explicit
error gating, replay and retention of failure history after recovery. Source coverage
integration and final whole-PR checks remain open; alert incident recovery remains part
of the subsequent webhook implementation.


Egress and ingress poll health is now wired into source coverage. Missing identities,
absent ingress state and unknown native enum values remain partial. Failed reads remain
unavailable; good empty snapshots do not clear terminal event findings. Both media sources
remain partial until the scoped historical media checkpoint is complete, even after a
successful poll. Native storage coverage and event-preservation regressions, native-state
classification tests, Go lint and SQL generation consistency checks pass. The implementation
is entering final whole-PR checks and review; the roadmap task stays unchecked until merge.
