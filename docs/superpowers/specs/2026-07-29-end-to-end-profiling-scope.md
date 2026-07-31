# VoiceGateway: end-to-end profiling — data model + build scope

Transforming VoiceGateway from an **inference-layer** profiler into an **end-to-end** voice-agent profiler (SIP → SFU → dispatch → agent → inference → infra).

Verified against the installed venv while writing: `livekit-api 1.1.0`, `livekit-protocol 1.1.7`, `livekit-agents 1.5.7`, `prometheus-client 0.25.0` (transitive, undeclared), **`boto3` MISSING**. Alembic head: `06836270c254` (a two-parent merge of `b7d2f4a9c1e3` + `d2f6b8a1c3e5`).

---

## 1. The problem, in the code

`requests` is the atomic unit (`models/request_model.py`). `sessions` is **derived** — UPSERT'd inside `request_log_repository.log_request`, gated on `if record.session_id:`. Nothing calls `log_request` except inference capture paths, so **a call that runs no inference does not exist anywhere in the schema.**

Worse: the **LiveKit room — the only identifier every layer 1–6 shares — is not a column.** `attach` stamps it into `requests.metadata` JSON, and the only way to query by it is a non-sargable `metadata LIKE` scan (`get_requests_for_room`).

Making `calls` first-class, keyed on the room, written by paths that never touch inference, fixes both.

## 2. What is actually observable (this bounds the whole design)

Verified by introspecting the installed SDK. **Do not design columns that pretend otherwise.**

| Source | Gives you | Blind to |
|---|---|---|
| **Webhooks** — `room_started/finished`, `participant_joined/left`, `participant_connection_aborted`, `track_published/unpublished`, `egress_*`, `ingress_*` | Room + participant + track lifecycle. `ParticipantInfo.attributes` carries `sip.callID`, `sip.callStatus`, `sip.phoneNumber`, `sip.trunkID`, `sip.ruleID`. `disconnect_reason` includes **`SIP_TRUNK_FAILURE`, `USER_UNAVAILABLE`, `USER_REJECTED`** — real layer-1 failure causes | **No `track_subscribed` event.** No `participant_attributes_changed`. |
| **`livekit-sip` prometheus** | Fleet aggregates: `invite_requests_raw`, `invite_requests`, `invite_accepted`, `calls_active`, `calls_terminated{status}`, `call_duration` | Anything per-call |
| **`livekit-server` prometheus** | `room_total`, `participant_total`, `packet_total/bytes{direction,transmission,country}`, `nack_total`, node CPU/load — all with `node_id`/`node_type` labels | Anything per-call |
| **Agent / load-worker self-report** | The only per-call view of subscribe latency and true INVITE→200 wall time | Only for calls it participates in |

**Four things I assumed that are not available:**

1. **Subscribe latency is not server-observable.** There is no `track_subscribed` webhook. Only the subscribing process sees it.
2. **Per-call SIP response codes are not retrievable.** `SIPCallInfo` carries `call_status_code`, `disconnect_reason`, `audio_codec`, `pcap_file_link` — but `livekit-api 1.1.0`'s `SipService` exposes **only** trunk / dispatch-rule / create-participant / transfer. No list-or-get. If a `ListSIPCallInfo` RPC ever ships, this becomes a small additive feature.
3. **Per-call RTP loss / jitter / MOS does not exist server-side.** `livekit_packet_*` and `nack_total` are node counters. `sfu.py` already hardcodes `loss_pct = 0.0` and is honest about it. **Reserve no column** — an empty column invites a UI that renders `0.0` as "no loss".
4. **There is no `node_id` awareness anywhere in the tree.** Layer 7 correlates by `(node, time window)`, never by FK to a call.

## 3. The schema

```
calls                                  ← created by ANY event, from webhook or loadgen
  id
  room_sid      UNIQUE NULL            ← the TRUE per-instance key (RM_*)
  room_name     NULLABLE               ← best-effort join key; NULL is legitimate
  origin        webhook|loadgen|agent
  attempt_id    UNIQUE NULL            ← loadgen correlation
  run_id        NULL
  project, tenant_id, agent_id, channel, direction
  started_at_ms, ended_at_ms, duration_ms, end_reason, num_legs, is_probe
  answer_latency_ms                    ← THE headline number
  answer_latency_source                ← how it was derived (see §4)

call_legs                              ← one per participant
  call_id (idx), participant_sid (UNIQUE with call_id)
  identity, kind (SIP|AGENT|STANDARD), region
  joined_at_ms, left_at_ms, disconnect_reason, is_publisher
  attributes_json                      ← sip.* keys only
  first_audio_track_at_ms, audio_track_sid, audio_codec

call_events        ← layer 1-3 state transitions, timestamped
node_samples       ← layer 7, correlated by (node, time), NOT by FK
sessions           ← EXISTS, gains nullable room_name + call_id
turns / requests   ← EXIST, unchanged (layers 5-6)
```

**Five decisions worth defending:**

- **`room_name` is NULLABLE.** A `503` on INVITE never creates a room — and that failure is the *most important row* in a load test. Key on `room_sid`; treat `room_name` as best-effort. A deployment pinning one fixed room name would otherwise collapse two concurrent calls into one.
- **Select-then-update, not native `ON CONFLICT`.** Copying the documented reason in `workers_repository.upsert_heartbeat`: a NULL `tenant_id` makes on-conflict duplicate rows on **both** SQLite and Postgres.
- **`upsert_call` must create-if-missing from *any* event.** Webhook delivery is neither ordered nor exactly-once.
- **Forward-only. No backfill, no repair CLI.** A `calls` row can only exist from the moment the receiver is deployed, so an older session has nothing to join to. This deletes an entire workstream (no `json_extract` dialect branch, no batched migration scan).
- **One linear alembic chain.** Head is already a two-parent merge; **assign all revision ids up front** and chain them, so implementing out of order still yields one head.

## 4. The headline correlation

Because **`livekit-sip` withholds `200 OK` until it subscribes to an audio track**, the agent's first published audio track *gates the caller's ring time*. So:

```
answer_latency_ms ≈ agent_track_published_at_ms − caller_joined_at_ms
```

That is a **webhook-only, zero-instrumentation proxy** for caller-visible answer latency, upgradeable to the real number when an agent or load worker reports it directly.

**Store `answer_latency_source` alongside it.** Precedence: `sipp_rtd` (true INVITE→200 wall time) > agent self-report > webhook proxy. One number, one column, one computation — nothing else in the product computes answer latency. Storing the provenance next to the value is what keeps it honest.

This is the product claim: *"the caller heard 4.1 s of ring because the agent took 3.8 s to publish audio, of which 2.9 s was LLM cold start."* Nothing on the market correlates layers 1→6 like that.

## 5. Ingest wiring (follows the existing pattern exactly)

Per-table pattern, smallest complete precedent to copy is `agent_probe_results`:
`models/<x>_model.py` → **re-export from `models/__init__.py`** → `alembic/versions/<rev>.py` → `repository/<x>_repository.py` → passthrough on `services/storage_service.py` → branch in `services/retention_service.py`.

- **Webhook receiver** — new router under `server/api/`. `livekit.api.WebhookReceiver` + `TokenVerifier` both exist in the installed SDK. **The signature check is the only thing guarding this endpoint** — it must run before any parsing, and no missing-creds branch may bypass it.
- **Prometheus scraper** — periodic background worker modeled on `middleware/agent_observations_worker_middleware.py` (~110 lines). Pulls `livekit-server` + `livekit-sip` `prometheus_port` and `node_exporter` into `node_samples`.
- **Agent/worker self-report** — `POST /v1/calls/observations`. Must be **fire-and-forget onto a bounded queue with a drop counter**, single background flusher, `VG_DISABLE_CALL_OBSERVATIONS` kill switch, every hook `try/except`-wrapped. A synchronous POST in the agent's job-start path would add latency to the exact thing being measured.

**No frontend changes in the ingest tier** — these endpoints are called by LiveKit and by agents, never the SPA, so `demoFixtures.ts` stays untouched. That boundary is deliberate.

## 6. Milestones

| M | Name | Effort | Demoable as |
|---|---|---|---|
| **M0** | Stop throwing away what we already measure | L+M | Diagnostics with 5 tabs: real agent roster, SFU baseline, ramp curve with knee, saturation banner, error-class bars |
| **L0** | SIPp spike — **blocking gate, not a feature** | S | One containerised SIPp call to livekit-sip: 200 OK, two-way audio, correct SDP `c=`, **over TLS/SRTP** |
| **M1** | The call becomes first-class | M+M+S | Place a call with **zero inference**. A `calls` row exists, legs timelined, `disconnect_reason = SIP_TRUNK_FAILURE` readable. Today: nothing exists |
| **M2** | The correlation ships | M+M+M+S | The one-sentence output in §4, on screen |
| **M3** | Gates, reports, client deliverable | M+M+S | `voicegw livekit check --strict` fails CI naming `answer_latency_p95_ms`; self-contained HTML report |
| **M4** | Node + infra layer | L+S | The knee at 25 clients correlated with `filefd_allocated` hitting `filefd_maximum` on one host |
| **M5** | BYO-compute load orchestration | M+L+M+L+M+M | 500 concurrent calls in the operator's own AWS account, cancellable, **priced before launch** |
| **M6** | Prometheus exposition | S | `curl /v1/metrics \| grep voicegw_diag_gate_status` |

**M0 needs no migration and no backend risk** — it's pure recovered value: the backend already returns agent rosters, SFU rtt/ramp/knee, and per-agent STT/LLM/TTS splits, and `Diagnostics.tsx` throws them away (`DiagnosticCheckResult.result` is typed `unknown`). Ship it first.

**M5 is roughly as large as M0–M4 combined**, and it's the only milestone optional to the monitor-first thesis. Ship M0–M3 before committing.

**Critical path: T1 → T2 → T5 → C1.** Everything the product claims as new depends on those four.

## 7. Lambda is rejected, not deferred

You proposed Lambda for BYO-compute. It doesn't survive scrutiny:

- **15-minute cap** turns a 24 h soak into 96 disjoint bursts — sustained concurrency is never actually measured, which is the whole point.
- **Per-invocation ENI churn** moves the SIP source and the SDP media IP between segments, breaking carrier trunk allowlists.
- **GB-seconds at 500 × 24 h** is an order of magnitude over Fargate.

**Use Fargate/ECS tasks.** Same BYO-compute property (user's account, user's bill, ephemeral), no execution cap, stable ENI per task. EC2/spot is cheaper for long runs but adds AMI + user-data + ASG + termination surface a solo maintainer shouldn't own in v1.

**Runs cap at 24 h.** The client's 7-day requirement is an *observation* window, met by the scrape worker — not by 7 days of continuous synthetic load. That's the single largest scope reduction here.

## 8. Sharp edges

- **`models/__init__.py` re-export failures are silent.** Miss one and `SQLModel.metadata` never sees the table; autogen and `create_all` both skip it and nothing errors.
- **`_UPSERT_SESSION_PG` is derived by string replace** (`request_log_repository.py:100` does `.replace("INSTR(", "STRPOS(")`). Any edit to the SQLite text must be checked against the Postgres derivation or the two drift silently.
- **`demoFixtures.ts` throws, it does not degrade.** `demoFetch` ends in `throw new Error(...)`. And the existing `DIAG_RUNS` fixtures are *invented shapes* matching nothing `RealProbes` returns — typed rendering against them yields blank cards, so they must be rewritten in the **same commit** as the tab work.
- **No frontend test runner.** No test script, no vitest. Verification is `tsc -b` + `vite build` + `vite build --mode demo`.
- **`MAX_POLLS = 180` at 2 s exactly equals `_OVERALL_RUN_TIMEOUT_SECONDS = 360`.** Coupled constants. Any run over six minutes needs both raised **and** run persistence.
- **The ContextVar hazard, twice.** `session_id` and `tenant` are ContextVars; `attach.register_components` keeps process-global singletons. N concurrent jobs in one process without `contextvars.copy_context()` merge into one `session_id`. The mock participant avoids it by never calling `attach()` — if per-job telemetry is added, each job body must run inside `copy_context()`.
- **`loss_pct = 0.0` is hardcoded in `sfu.py`.** Any loss series built on it renders a fabricated clean bill of health. Suppress the series; add no column.
- **`find_knee` returns `None` for two opposite outcomes** — nothing breached, or the *first* tier breached. A chart that doesn't distinguish them labels a total failure as a pass.
- **Counter resets look like huge negative rates.** A livekit-server restart zeroes every `_total`. Read-time diffing must emit null when `current < previous`.
- **One process, one port.** Webhook receiver, load poller, scrape worker, and dashboard all share one uvicorn. During a 500-call burst the UI will feel dead unless ingest paths are bounded and measured. A synchronous SQLite writer absorbing a 2500-event burst is the second-highest risk in the plan — **measure it in M1, don't assume WAL absorbs it.**
- **A crashed control plane must not leave 500 calls billing.** `VG_DEADLINE_AT` in the worker + ECS `stopTimeout`, tested by killing the daemon mid-run.
- **Retention is mandatory.** Scraping ~10 nodes at 15 s is ~57k rows/day. Downsample on write or trim in the rollup worker.

## 9. Not being built

| Not built | Why |
|---|---|
| Any gossipper code, scenario or config | **AGPL-3.0 vs MIT.** Clean-room from the SIPp reference and RFC 3261. apt-installing the GPL SIPp *binary* alongside an MIT wheel is mere aggregation and is fine. |
| Per-call RTP loss / jitter / MOS / DTMF | Not observable server-side. Needs a client reading its own `getStats()`. |
| Per-call SIP response codes, INVITE→200 from LiveKit, provisional responses, BYE headers, negotiated SDP | Blocked on LiveKit: no `ListSIPCallInfo` RPC in `livekit-api 1.1.0`. |
| Backfill of `sessions.room_name` / `call_id`, any repair CLI | Zero value — an older session has nothing to join to. |
| A separate `load_call_attempts` table | Merged into `calls`. Two call-level tables means two percentile paths and two UIs disagreeing about one run. |
| `livekit_diag/distributed.py` Coordinator (388 lines, written, unused) | An absolute `VG_START_AT` in the task env achieves the synchronized start with no inbound path to a control plane that is usually a laptop outside the VPC. `aggregate_vantages` is reused as a pure fold; the HTTP barrier is not. |
| Reusing `/v1/ingest` or `RemoteCollectorSink` for load results | Those carry `RequestRecord` and get re-rated against the cost card. Call-level SIP records would corrupt the cost model. |
| ClickHouse / DuckDB mirrors of the new tables | Premature before the SQLite shape survives a real 500-call run. |
| SSE / WebSocket push for calls or load | The dashboard has zero WebSocket by design. Calls and load poll. |
| PDF export, branded/scheduled reports, gate alerting | Operators who run Prometheus alert on the exposition series. VoiceGateway does not learn to alert. |
| GCP / Azure targets, outbound PSTN dialing | Unbounded surface for a solo maintainer; outbound exercises the wrong direction for an inbound-capacity question. |
| VoiceGateway generating load from its own process | Monitor-first. The daemon launches, watches, cancels, and ingests. **It never dials.** |

## 10. Resolve before starting

1. **`is_probe` cannot be set for load traffic.** For SIP-originated calls the room name comes from the operator's dispatch rule, so a probe prefix can only be *checked*, not enforced. Decide the discriminator now (`run_id` via `X-VG-Attempt` → participant attributes, requiring `headers_to_attributes` on the trunk; or a dedicated trunk id on the compute target). Without it, **load traffic silently pollutes production percentiles for every call that answers.**
2. **Correlation coverage is unmeasured.** `sessions.room_name` comes from `record.metadata.get("room")`, which only exists when `attach` resolved a LiveKit job context — web and Pipecat sessions have none. **Ship a correlation-rate number from day one**, or you won't know the join is failing.
3. **Percentiles.** Three algorithms coexist and disagree on small samples. New surfaces standardise on `utils/percentiles.compute_percentiles`; no retrofit (it would move published p95 numbers). **Any percentile from fewer than 10 samples must render as "max of N", not "p95"** — `MAX_LATENCY_TRIALS = 3`, so today's diagnostics p95 is *always* the max of 3.
4. **Two verdict implementations disagree** (`service.py:_verdict` vs `report.py:check_json`). Collapse into `livekit_diag/gates.py`; stricter reading wins. This **changes `voicegw livekit check` exit codes** for existing users — needs a version-bump decision and a CHANGELOG entry.
