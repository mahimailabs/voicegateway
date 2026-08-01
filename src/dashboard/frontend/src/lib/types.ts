export interface StatusResponse {
  // Live server version (footer pill). Optional so older servers still type-check.
  version?: string;
  providers: Record<string, { configured: boolean; type: string }>;
  models: Record<string, { modality: string; provider: string }>;
  fallbacks: Record<string, string[]>;
}

export interface OverviewResponse {
  total_requests: number;
  total_cost_today: number;
  total_cost_all: number;
  active_models: number;
  providers_configured: number;
}

/** One day-bucket of the Overview trend series (GET /api/costs/by-day). */
export interface CostByDayPoint {
  day: number; // UTC start-of-day epoch seconds
  cost: number;
  requests: number;
}

export interface CostsResponse {
  period: string;
  project: string | null;
  total: number;
  by_provider: Record<string, { cost: number; requests: number }>;
  // by_model entries carry the pricing_source attribution string
  // since v0.0.5 (Q7); /api/costs sets include_pricing_source=true
  // by default.
  by_model: Record<
    string,
    { cost: number; requests: number; pricing_source?: string }
  >;
  by_project: Record<string, { cost: number; requests: number }>;
  // Top-level catalog summary; the /v1 endpoint sets it. The
  // dashboard mirror does not yet, so the field is optional.
  pricing_sources?: { llm?: string; stt?: string; tts?: string };
}

export interface PercentileBucket {
  // Known percentiles are optional — the API only emits what the config
  // asks for, so callers must treat missing keys as "unknown".
  p50?: number | null;
  p95?: number | null;
  p99?: number | null;
  // Extra keys tolerated so non-default percentile lists (e.g. p99_9)
  // still round-trip through the type.
  [key: string]: number | null | undefined;
}

export interface LatencyStats {
  avg_ttfb_ms: number;
  avg_latency_ms: number;
  request_count: number;
  ttfb_percentiles: PercentileBucket;
  latency_percentiles: PercentileBucket;
}

export type LatencyResponse = Record<string, LatencyStats>;

export interface LogRecord {
  id: string;
  timestamp: number;
  modality: string;
  model_id: string;
  provider: string;
  project: string;
  cost_usd: number;
  pricing_source: string | null;
  ttfb_ms: number | null;
  total_latency_ms: number | null;
  status: string;
}

// v0.0.5 sessions API. The list endpoint returns SessionRow[]; the
// detail endpoint extends each row with the per-modality breakdown
// and the deduplicated providers list, joined from the requests
// table at read time.
export interface SessionRow {
  id: string;
  project: string;
  started_at: string;       // ISO 8601 UTC
  ended_at: string | null;  // ISO 8601 UTC; null when no requests yet
  modalities: string[];
  total_cost_usd: number;
  request_count: number;
  // v0.4.0 (REQ-VG-TENANT-001). NULL renders as the muted
  // "unattributed" pill. Older session rows written before v0.4.0
  // simply omit the field; the JSON response leaves it absent.
  tenant_id?: string | null;
  // v0.5.0 (REQ-VG-ROUTE-003). NULL on pre-v0.5.0 sessions or
  // when the router never ran. The dashboard renders NULL as
  // '-' in the routing strip.
  routed_llm?: string | null;
  routed_tts?: string | null;
  budget_ms?: number | null;
  budget_overrun?: boolean | null;
}

export interface SessionDetail extends SessionRow {
  by_modality: Record<string, { cost: number; request_count: number }>;
  providers: string[];
}

/** One captured transcript turn for a call (from /api/sessions/{id}/transcript). */
export interface TranscriptTurn {
  session_id: string;
  seq: number;
  role: string; // 'user' | 'agent'
  text: string;
  created_at?: string | null;
}

export type SessionOrderBy =
  | 'started_at_desc'
  | 'started_at_asc'
  | 'cost_desc'
  | 'cost_asc';

// v0.2.0 voice-conversation metrics types. The /api/metrics handler
// returns `MetricsAggregate`; per-session drilldowns return TurnRow
// and DeadAirEvent lists. T17 extends api.ts with typed fetchers for
// these endpoints; T13 ships the minimal type the Metrics.tsx page
// needs to compile.

export interface MetricsAggregate {
  window: {
    days: number;
    since: string;
    until: string;
  };
  filter: {
    project: string | null;
  };
  session_count: number;
  measured_session_count: number;
  per_minute_cost_usd_avg: number | null;
  response_speed_ms: {
    p50: number | null;
    p95: number | null;
  };
  talk_over_rate: number | null;
  dead_air_event_count: number;
}

export interface TurnRow {
  session_id: string;
  turn_index: number;
  caller_speak_start_ms: number;
  caller_speak_end_ms: number;
  agent_speak_start_ms: number | null;
  agent_speak_end_ms: number | null;
  response_speed_ms: number | null;
}

export interface DeadAirEvent {
  session_id: string;
  started_at_ms: number;
  duration_ms: number;
  threshold_used_ms: number;
}

// v0.3.0 conversation-replay types. The /api/sessions/{id}/replay
// endpoint returns a time-ordered list of `ReplayEvent` rows
// covering all four modalities (stt/llm/tts/state). T14 extends
// api.ts with the typed fetcher; T11 ships the minimal type the
// Replay.tsx page needs to compile.

export interface ReplayEvent {
  session_id: string;
  modality: 'stt' | 'llm' | 'tts' | 'state';
  t_ms: number;
  payload: Record<string, unknown>;
  provider: string;
  cost_usd: number | null;
}

export interface ReplayResponse {
  session_id: string;
  events: ReplayEvent[];
}

export interface StateSnapshot {
  system_prompt: string;
  message_history: Array<Record<string, unknown>>;
  tool_call_in_flight: Record<string, unknown> | null;
  structured_output_collected: Record<string, unknown> | null;
}

export interface RetentionWindow {
  project_id: string;
  retention_days: number;
}

// ----------------------------------------------------------------------
// v0.4.0 multi-tenant attribution (REQ-VG-TENANT-001..004).
// ----------------------------------------------------------------------

/**
 * Tenant filter URL convention:
 *   - `null`: no filter (everything)
 *   - `""`: scope to the unattributed bucket (sessions with NULL tenant_id)
 *   - any other string: that exact tenant
 */
export type TenantFilter = string | null;

// ---------------------------------------------------------------------------
// Phase 2 fleet: per-agent dimension (mirrors the tenant types above).
// ---------------------------------------------------------------------------

/** One row in the agent index (the fleet table + agent-filter feed). */
export interface AgentRow {
  agent_id: string;
  /** Friendly worker label from the roster (matches Server > Fleet); null for a
   * telemetry-only agent, where the UI falls back to agent_id. */
  agent_name?: string | null;
  request_count: number;
  total_cost_usd: number;
  /** Epoch seconds of the agent's most recent request, or null. */
  last_seen: number | null;
  error_rate: number;
  /** p95 total-latency ms, merged in by /api/agents; null when no samples. */
  p95_latency_ms?: number | null;
  /** RSS as a percent of the worker's memory ceiling, merged from the live
   * roster; null when the agent is not a heartbeating worker or sent no sample. */
  memory_pct?: number | null;
  /** CPU as a percent of the machine's capacity, from the live roster; null when
   * the agent is not a heartbeating worker or sent no sample. */
  cpu_pct?: number | null;
  /** CPU + memory snapshot from the live roster. Percentages are shares of the
   * machine's capacity (utilized; left = 100 - this). All null for a
   * telemetry-only agent (no heartbeat) or a sample that failed. */
  resources?: {
    cpu_pct: number | null;
    memory_pct: number | null;
    memory_rss_bytes: number | null;
    memory_total_bytes: number | null;
  };
  /** Last-seen model per modality, from /api/agents; a modality is null when unseen. */
  models?: { stt: string | null; llm: string | null; tts: string | null };
  /** Average first-byte latency (ms) per modality over 24h, for the card
   * waterfall; a modality is null when it metered nothing. Only STT/LLM/TTS are
   * measured (network hops / turn detection are not metered). */
  latency_ms?: { stt: number | null; llm: number | null; tts: number | null };
  /** Live roster presence: 'idle' | 'busy' | 'offline'; null when the agent is
   * telemetry-only (not currently a registered/heartbeating worker). */
  fleet_status?: string | null;
  /** Whether this agent's card can place a probe, and why not when it cannot.
   * Absent on older servers, which the UI reads as "no play button". */
  probe?: AgentProbeBlock;
  /** The agent's last cached probe (run once, refreshable), for the Agents-page
   * latency graph. Null until this agent has been probed. */
  latency_probe?: {
    components: AgentProbeResult['components'];
    e2e: AgentProbeResult['e2e'];
    cost_usd: number | null;
    models: AgentProbeResult['models'] | null;
    /** What the probe actually ran with, so the Overview card can render the
     * cached sample verbatim without re-billing. */
    mode: ProbeMode | null;
    dispatch_name: string | null;
    error: string | null;
    /** Epoch seconds the probe ran, so the UI can say how fresh it is. */
    created_at: number;
  } | null;
}

export type ProbeMode = 'explicit' | 'automatic';

/**
 * Probe eligibility for one agent, from `/api/agents`.
 *
 * `dispatch_name` is the LiveKit `Job.agent_name` VoiceGateway observed on a
 * call the agent actually ran. It is set at worker registration inside the
 * agent's own process, so it can only be read back, never invented: an agent
 * with no observed job comes back ineligible with the reason, not with a guess.
 * `""` is an answer, not a blank: that worker is on automatic dispatch.
 */
export interface AgentProbeBlock {
  eligible: boolean;
  dispatch_name: string | null;
  mode: ProbeMode | null;
  /** Why the button is disabled, or a caveat on an eligible probe (e.g. more
   * than one automatic-dispatch worker online). Null when there is nothing to say. */
  reason: string | null;
}

/**
 * `POST /api/agents/{id}/probe`: one real call, measured.
 *
 * Times are SECONDS. Anything that could not be measured is null, never zero:
 * a null `cost_usd` means this host cannot know (the agent ships its telemetry
 * elsewhere), which is a different claim from "the call was free".
 */
export interface AgentProbeResult {
  agent_id: string;
  dispatch_name: string;
  mode: ProbeMode;
  /** The throwaway `vg-probe-` room the call ran in; null when no turn completed. */
  room: string | null;
  trials: number;
  /** End-to-end reply time in seconds, measured by the synthetic client itself.
   * Null when no turn completed (see `error`). */
  e2e: {
    avg: number;
    p50: number;
    p95: number;
    min: number;
    max: number;
    trials: number;
  } | null;
  /** Per-leg split in seconds, read back from the rows the agent wrote for this
   * room. Null when the agent is not writing telemetry to this host. */
  components: {
    eou?: number;
    stt?: number;
    stt_ttfp?: number;
    stt_transcription_delay?: number;
    llm_ttft?: number;
    tts?: number;
  } | null;
  cost_usd: number | null;
  /** The provider/model this call ran per leg, for the split's hover labels.
   * Null per leg the call did not produce. */
  models: { stt: string | null; llm: string | null; tts: string | null };
  error: string | null;
}

/** Aggregates for the implicit `agent_id IS NULL` bucket. */
export interface AgentUnattributedAggregates {
  request_count: number;
  total_cost_usd: number;
  last_seen: number | null;
  error_rate: number;
}

/** `/api/agents` response shape. */
export interface AgentsResponse {
  agents: AgentRow[];
  unattributed: AgentUnattributedAggregates;
}

/**
 * Agent filter URL convention (mirrors TenantFilter):
 *   - `null`: no filter (everything)
 *   - `""`: scope to the unattributed bucket (requests with NULL agent_id)
 *   - any other string: that exact agent
 */
export type AgentFilter = string | null;

/** One row in the API Keys table; the bcrypt hash never appears. */
export interface ApiKey {
  id: number;
  key_prefix: string;
  name: string;
  tenant_id: string | null;
  issued_by: string | null;
  issued_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** `POST /api/api_keys` response: the only place plaintext appears. */
export interface CreatedApiKey {
  id: number;
  plaintext: string;
  row: ApiKey;
}

// ----------------------------------------------------------------------
// v0.5.0 cross-modality routing + white-label branding (REQ-VG-ROUTE-001..004).
// ----------------------------------------------------------------------

/**
 * The router's pick for a session. Returned by ``route_session`` and
 * persisted to the ``sessions`` row's ``routed_llm`` / ``routed_tts``
 * / ``budget_ms`` / ``budget_overrun`` columns. STT comes from the
 * session's existing provider field.
 */
export interface RoutedTriple {
  stt: string;
  llm: string;
  tts: string;
  predicted_ms: number;
  budget_overrun: boolean;
}

/** White-label branding payload (REQ-VG-ROUTE-004). All fields optional. */
export interface ProjectBranding {
  logo_url?: string | null;
  accent_color?: string | null;
  product_name?: string | null;
}

/** ``GET /api/projects/{id}/branding`` response. */
export interface ProjectBrandingResponse {
  project_id: string;
  branding: ProjectBranding | null;
}

/** ``POST /api/projects/{id}/branding/logo`` response. */
export interface LogoUploadResponse {
  project_id: string;
  logo_url: string;
  bytes: number;
  format: 'PNG' | 'SVG';
}

// ---------------------------------------------------------------------------
// Diagnostics: LiveKit probe types (REQ-VG-DIAG-001).
// ---------------------------------------------------------------------------

/** Connection status from GET /api/diagnostics/creds. */
export interface DiagnosticsCreds {
  configured: boolean;
  url: string | null;
}

/** The checks a run can request. Also the key each result is filed under. */
export type DiagCheckName = 'agents' | 'sfu' | 'sfu_load' | 'latency';

/**
 * One agent seen inside a live room (`livekit_diag.admin.AgentRow`).
 *
 * This is the IN-ROOM population only: LiveKit's server API reports a worker
 * only once it has joined a room, so an idle registered worker never appears
 * here. `DiagAgentsResult.roster` is the other half.
 */
export interface DiagAgentRow {
  agent_name: string;
  room: string;
  identity: string | null;
  /** 'active' (joined the room) or 'dispatched' (assigned, has not joined yet). */
  state: string;
  /** Non-agent participants in the same room. */
  humans: number;
  /** Seconds since the agent joined; null when the server reported no join time. */
  age_s: number | null;
}

/**
 * One registered worker from the collector's heartbeat roster.
 *
 * Every field is optional because this JSON is produced by whatever collector
 * the operator pointed at (`VOICEGW_COLLECTOR_URL`), not by this build, so the
 * UI must tolerate a missing key rather than render `undefined`.
 */
export interface DiagRosterWorker {
  agent_id?: string | null;
  agent_name?: string | null;
  /** idle | busy | offline, as reported by the worker's own heartbeat. */
  status?: string | null;
  region?: string | null;
  host?: string | null;
  version?: string | null;
  active_sessions?: number | null;
  /** Epoch seconds of the last heartbeat. */
  last_seen?: number | null;
}

/** `agents` check result. */
export interface DiagAgentsResult {
  agents: DiagAgentRow[];
  /**
   * The heartbeat roster, or null when NO collector is configured.
   *
   * The distinction is load-bearing: `null` means nobody was asked (so the UI
   * says how to enable it), `[]` means the collector was asked and reported no
   * workers (or could not be reached). Collapsing the two would render "not
   * configured" as "you have zero workers".
   */
  roster: DiagRosterWorker[] | null;
}

/** The SFU baseline: 2 synthetic clients talking through the SFU. */
export interface DiagSfuBaseline {
  /** Data-channel round trip through the SFU, in ms (no clock sync needed). */
  rtt_ms: number;
  /**
   * NOT A MEASUREMENT. `sfu.py` hardcodes 0.0 because the SDK does not expose
   * per-connection loss here. Never render it as a number and never build a
   * series on it: a flat 0% loss line reads as a clean bill of health nobody
   * measured. `quality` is the coarse signal that is real.
   */
  loss_pct: number;
  /** Connection quality from the SDK: Excellent | Good | Poor | Lost | Unknown. */
  quality: string;
}

/** One ramp tier: `clients` synthetic clients in one room for the step duration. */
export interface DiagSfuStep extends DiagSfuBaseline {
  clients: number;
}

/**
 * The PROBER host's own load during the ramp (not the SFU's).
 *
 * Null fields mean "not sampled", never "idle": the backend maps
 * `ResourceReport`'s 0.0 defaults to null so an unsampled CPU cannot render as a
 * host with infinite headroom. `saturated` is null for the same reason - with no
 * CPU sample, saturation is unknown, not false.
 */
export interface DiagResourceReport {
  cpu_peak: number | null;
  mem_peak_mb: number | null;
  net_kbps_up: number | null;
  saturated: boolean | null;
  per_client: { cpu_pct: number | null; kbps_up: number | null };
  /** Clients this host could sustain before it is CPU-bound; null when unknown. */
  sustainable_n: number | null;
}

/** `sfu` and `sfu_load` check result (the ramp is empty for the plain `sfu` check). */
export interface DiagSfuResult {
  baseline: DiagSfuBaseline;
  ramp: DiagSfuStep[];
  /**
   * The last healthy client count before the first tier that broke the budget.
   *
   * **Null is ambiguous on its own**: `find_knee` returns null both when nothing
   * breached AND when the FIRST tier breached. Compare `ramp` against
   * `target_rtt_ms` to tell a clean ramp from a total failure.
   */
  knee: number | null;
  /** The rtt budget the knee was computed against, in ms. */
  target_rtt_ms: number;
  /** Null when the run did not include the load check (no ramp, nothing sampled). */
  resource: DiagResourceReport | null;
}

/**
 * Per-leg split for one probed agent, in SECONDS, read back from the rows the
 * agent itself wrote for the probe room. A leg the run did not produce (or that
 * this host never saw) is absent - never zero.
 */
export interface DiagComponentSplit {
  eou?: number;
  stt?: number;
  stt_ttfp?: number;
  stt_transcription_delay?: number;
  llm_ttft?: number;
  tts?: number;
}

/**
 * End-to-end reply timings for one agent, in SECONDS.
 *
 * `p50`/`p95` come from `livekit_diag.latency.summarize` over at most
 * `MAX_LATENCY_TRIALS = 3` samples. Three samples cannot support a percentile,
 * so the UI must render `max` labelled "max of N" - never "p95".
 */
export interface DiagLatencyStats {
  avg: number;
  p50: number;
  p95: number;
  min: number;
  max: number;
  /** Successful trials. 0 means nothing was measured for this agent. */
  trials: number;
}

export interface DiagLatencyAgent {
  agent: string;
  stats: DiagLatencyStats;
  /** Null when the agent does not write telemetry to this host. */
  components: DiagComponentSplit | null;
  /**
   * Why no trial answered, verbatim from `LatencyResult.error`: the same string
   * the CLI prints after "no successful probe" (a dispatch that reached no
   * worker, a connect that failed, a client that raised).
   *
   * Null means the probe recorded NO reason, and undefined means the run was
   * recorded before this field existed. Both are the same claim: nothing said
   * why. Neither is an empty reason, so render "no reply" for them and never a
   * blank explanation.
   *
   * This is remote text (a provider or the LiveKit server wrote it, not
   * VoiceGateway): render it as TEXT, never as markup, and let it wrap.
   */
  error?: string | null;
}

/** `latency` check result. */
export interface DiagLatencyResult {
  agents: DiagLatencyAgent[];
}

/** Any check's payload, for code that only reads `ok` / `error`. */
export type DiagCheckPayload = DiagAgentsResult | DiagSfuResult | DiagLatencyResult;

/**
 * Per-check result inside a DiagnosticRun's results map.
 *
 * `result` is present only when `ok`; a failed check carries `error` instead.
 */
export interface DiagnosticCheckResult<R = DiagCheckPayload> {
  ok: boolean;
  result?: R;
  error?: string;
}

/** The per-check map, keyed by check name: only requested checks are present. */
export interface DiagnosticRunChecks {
  agents?: DiagnosticCheckResult<DiagAgentsResult>;
  sfu?: DiagnosticCheckResult<DiagSfuResult>;
  sfu_load?: DiagnosticCheckResult<DiagSfuResult>;
  latency?: DiagnosticCheckResult<DiagLatencyResult>;
}

export interface DiagnosticRunResults {
  checks: DiagnosticRunChecks;
  verdict: string;
}

/** One diagnostic run record from the backend. */
export interface DiagnosticRun {
  run_id: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  checks: string[];
  config: Record<string, unknown>;
  results: DiagnosticRunResults | null;
  verdict: string | null;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  ended_at: string | null;
}

// ---------------------------------------------------------------------------
// Server: live LiveKit deployment topology + fleet, cost-annotated.
// ---------------------------------------------------------------------------

/** LiveKit control-plane connection state. `reachable` is null until probed. */
export interface ServerConnection {
  configured: boolean;
  url: string | null;
  reachable: boolean | null;
}

/** One agent inside a live room. */
export interface ServerRoomAgent {
  agent_name: string;
  identity: string | null;
  state: string; // "active" (joined) or "dispatched" (assigned, not joined)
  age_s: number | null;
}

/** A live room, annotated with VG's own metered cost over the last 24h. */
export interface ServerRoom {
  name: string;
  humans: number;
  agents: ServerRoomAgent[];
  cost_usd: number;
  request_count: number;
  p95_latency_ms: number | null;
}

/** One registered worker from the local heartbeat roster. */
export interface ServerWorker {
  agent_id: string;
  agent_name: string;
  region: string | null;
  host: string | null;
  version: string | null;
  status: string; // idle | busy | offline
  active_sessions: number;
  last_seen: number;
  memory_pct: number | null;
}

/** A section that may fail independently without blanking the page. */
export interface ServerSection {
  ok: boolean;
  error: string | null;
}

export interface ServerRoomsSection extends ServerSection {
  rooms: ServerRoom[];
}

export interface ServerFleetSection extends ServerSection {
  workers: ServerWorker[];
  counts: { total: number; idle: number; busy: number; offline: number };
}

/** An egress job (recording / streaming). Secret fields are never included. */
export interface ServerEgressItem {
  egress_id: string;
  status: string;
  source_type: string;
  room_name: string;
  started_at: number;
}

/** An ingress endpoint. stream_key / url (secrets) are never included. */
export interface ServerIngressItem {
  ingress_id: string;
  name: string;
  input_type: string;
  room_name: string;
  status: string;
}

export interface ServerSipInboundTrunk {
  trunk_id: string;
  name: string;
  numbers: string[];
}

export interface ServerSipOutboundTrunk {
  trunk_id: string;
  name: string;
  address: string;
  transport: string;
  numbers: string[];
}

export interface ServerSipDispatchRule {
  rule_id: string;
  name: string;
  trunk_ids: string[];
}

export interface ServerEgressSection extends ServerSection {
  items: ServerEgressItem[];
}

export interface ServerIngressSection extends ServerSection {
  items: ServerIngressItem[];
}

export interface ServerSipSection extends ServerSection {
  inbound: ServerSipInboundTrunk[];
  outbound: ServerSipOutboundTrunk[];
  dispatch_rules: ServerSipDispatchRule[];
}

/** GET /api/server/overview: a read-only deployment snapshot, per section. */
export interface ServerOverview {
  generated_at: number;
  connection: ServerConnection;
  rooms: ServerRoomsSection;
  egress: ServerEgressSection;
  ingress: ServerIngressSection;
  sip: ServerSipSection;
  fleet: ServerFleetSection;
}

// ---------------------------------------------------------------------------
// Calls: the call itself (SIP -> SFU -> dispatch -> agent), not the inference.
//
// These mirror the reader dataclasses in `repository/calls_repository.py`
// (`CallRow`, `CallLegRow`) field for field, on purpose. A field invented here
// that the repository does not serve renders as a permanently blank row that
// still compiles, which is the failure mode the demo fixtures already taught
// this codebase once.
// ---------------------------------------------------------------------------

/**
 * Which clock produced one leg timestamp (`call_legs.*_source`).
 *
 * `agent` and `loadgen` are processes that took part in the call reporting
 * their own millisecond clock. `webhook` is LiveKit's delivery, whose
 * `created_at` is whole SECONDS, so anything derived from it carries up to a
 * second of truncation. `null` is "the writer did not say", which
 * `calls_repository.SELF_REPORT_ORIGINS` treats as webhook precision - and so
 * does the UI, because under-claiming precision is the safe direction.
 */
export type ClockSource = 'webhook' | 'agent' | 'loadgen';

/**
 * `calls.answer_latency_source`: the CLOSED set from
 * `calls_repository.ANSWER_LATENCY_SOURCES`, strongest first.
 *
 * The field on `CallRow` below is typed `string | null` rather than this union
 * so a name added server-side later renders as unknown provenance instead of
 * failing to type-check against a stale copy of the set.
 */
export type AnswerLatencySource = 'sipp_rtd' | 'agent_report' | 'webhook_proxy';

/** One `call_legs` row: one participant's slice of the call. */
export interface CallLegRow {
  id: number | null;
  call_id: string;
  participant_sid: string;
  identity: string | null;
  /** 'SIP' (the caller), 'AGENT', or 'STANDARD'. Null when never observed. */
  kind: string | null;
  region: string | null;
  joined_at_ms: number | null;
  left_at_ms: number | null;
  disconnect_reason: string | null;
  is_publisher: number | null;
  /** JSON object of the `sip.*` participant attributes only. */
  attributes_json: string | null;
  first_audio_track_at_ms: number | null;
  audio_track_sid: string | null;
  audio_codec: string | null;
  joined_at_source: ClockSource | null;
  first_audio_track_at_source: ClockSource | null;
}

/** One `calls` row. `answer_latency_ms` is THE headline number. */
export interface CallRow {
  id: string;
  room_sid: string | null;
  /** Best-effort. NULL is legitimate: a 503 on INVITE never creates a room. */
  room_name: string | null;
  origin: string;
  attempt_id: string | null;
  run_id: string | null;
  project: string;
  tenant_id: string | null;
  agent_id: string | null;
  channel: string | null;
  direction: string | null;
  started_at_ms: number | null;
  ended_at_ms: number | null;
  duration_ms: number | null;
  end_reason: string | null;
  num_legs: number;
  is_probe: number;
  /**
   * Caller-visible answer latency, or null when the recorded timestamps do not
   * support one. Computed ONLY by `calls_repository._derive_answer_latency`;
   * the UI displays it and never recomputes it.
   */
  answer_latency_ms: number | null;
  /** See `AnswerLatencySource`. Null exactly when `answer_latency_ms` is. */
  answer_latency_source: string | null;
}

/** A call with its legs, which is what the layer waterfall needs. */
export interface CallDetail extends CallRow {
  legs: CallLegRow[];
}

/** GET /api/calls */
export interface CallsResponse {
  calls: CallDetail[];
}

// ---------------------------------------------------------------------------
// Correlation rate (GET /api/correlation).
//
// Mirrors `session_repository.CorrelationRate` field for field. The endpoint is
// a pure passthrough and so is this type: nothing here is computed in the
// browser.
// ---------------------------------------------------------------------------

/**
 * `CorrelationRate.status`: the CLOSED set from
 * `session_repository.CORRELATION_STATUSES`.
 *
 * `unknown` is a real, distinct answer, not an error: with an empty denominator
 * there is no rate to publish, and neither 100% nor 0% would be true.
 */
export type CorrelationStatus = 'ok' | 'warn' | 'unknown';

/** GET /api/correlation: how often the sessions <-> calls join resolves. */
export interface CorrelationRate {
  /** Denominator: sessions that HAD a room and so should have joined. */
  eligible: number;
  /** Numerator: of those, the ones pointing at a call that exists. */
  correlated: number;
  /**
   * `correlated / eligible`, or null when `eligible` is 0. Null is NOT 0: it
   * means nothing was measured, and rendering it as 0% would report an
   * unmeasured deployment as a completely broken one.
   */
  rate: number | null;
  /** Uncorrelated because the room name matched more than one call. */
  ambiguous: number;
  /** Pointing at a call that has since been pruned. Not a correlation. */
  dangling: number;
  /** Sessions with no room at all (web, Pipecat). Out of BOTH sides. */
  no_room: number;
  /** The rate below which this is worth attention. Published, not assumed. */
  warn_threshold: number;
  status: CorrelationStatus;
}

// ---------------------------------------------------------------------------
// Node samples correlated to calls by TIME WINDOW (GET /api/nodes).
//
// Mirrors `node_correlation_repository` field for field. The endpoint is a pure
// passthrough and so is this type: nothing here is computed in the browser, and
// no `null` below may be rendered as a 0.
//
// The claim these types carry is OVERLAP, not attribution. `node_samples` has
// no `call_id` (layer 7 counters are node-wide), so a node listed against a call
// means only that rows exist for it inside that call's padded window.
// ---------------------------------------------------------------------------

/**
 * `WindowCorrelation.status`: the CLOSED set from
 * `node_correlation_repository.WINDOW_STATUSES`.
 *
 * Three DIFFERENT claims, and none of them is a zero:
 *
 * - `correlated`: at least one successful scrape overlaps the window.
 * - `scrape_failed`: rows exist in the window and every one of them is a failed
 *   scrape. The nodes were being watched and the watching did not work, which is
 *   emphatically not "the nodes were fine".
 * - `no_samples`: nothing was scraped in the window at all (no worker, no
 *   configured target, or the window predates the trim). We did not look; it is
 *   not "the fleet was idle".
 */
export type NodeWindowStatus = 'correlated' | 'scrape_failed' | 'no_samples';

/**
 * Which statistic a peak actually is, from the CLOSED set
 * `node_correlation_repository.PEAK_STATS`.
 *
 * Below 10 measured points a percentile is a story about three numbers, so the
 * peak is the maximum and says `max_of_n`. With nothing measured it is
 * `not_measured` and the value beside it is null.
 */
export type NodePeakStat = 'p95' | 'max_of_n' | 'not_measured';

/** The time window a correlation was computed over. Epoch milliseconds. */
export interface NodeWindow {
  /** `requested_start_ms - pad_ms`: the bound actually queried. */
  start_ms: number;
  /** `requested_end_ms + pad_ms`: the bound actually queried. */
  end_ms: number;
  /**
   * How much was added on EACH side. Load-bearing: the scrape grid is coarse
   * (15 s) next to a short call and the scrape host's clock is not the clock
   * that stamped the call, so without it a short call correlates to nothing.
   * A reader who cannot see this cannot tell "within 15 s of this call" from
   * "during this call", and must never be shown the padded region as the call.
   */
  pad_ms: number;
  /** What the caller asked about: the call's own span. */
  requested_start_ms: number;
  requested_end_ms: number;
}

/** One point-in-time column over the window. Gauges are never diffed. */
export interface NodeGaugeSummary {
  column: string;
  /** Rows whose value was actually present. A NULL is excluded, not zeroed. */
  samples: number;
  minimum: number | null;
  maximum: number | null;
  /** Last measured value in the window: what a saturation reading compares at. */
  latest: number | null;
  /** Null exactly when `peak_stat` is `not_measured`. Never 0 in its place. */
  peak: number | null;
  peak_stat: NodePeakStat;
}

/** One cumulative column over the window, as per-second rates. */
export interface NodeCounterRateSummary {
  column: string;
  /** In-window rate points. */
  points: number;
  /**
   * Points whose rate is null: the window's first point, a NULL at either end,
   * a non-positive time delta, or a counter RESET. The repository deliberately
   * does not say which, because that rule has exactly one implementation.
   * `unknown_points === points` means no rate here is sourceable. That is NOT
   * 0/s.
   */
  unknown_points: number;
  peak_per_second: number | null;
  peak_stat: NodePeakStat;
}

/** What one `(node, source)` looked like while the window was open. */
export interface NodeSamplesInWindow {
  node: string;
  source: string;
  samples: number;
  ok_samples: number;
  failed_samples: number;
  /** `outcome` -> count, so "5 rows, all timeouts" stays visible. */
  outcomes: Record<string, number>;
  first_sample_at_ms: number | null;
  last_sample_at_ms: number | null;
  /** Every gauge column, including the ones this source cannot measure. */
  gauges: Record<string, NodeGaugeSummary>;
  /** Every counter column, same. */
  counters: Record<string, NodeCounterRateSummary>;
  /** The read hit its row limit, so these summaries describe a PREFIX. */
  truncated: boolean;
}

/** Nodes whose samples OVERLAP a window. Not the nodes that served a call. */
export interface WindowCorrelation {
  window: NodeWindow;
  status: NodeWindowStatus;
  /** Empty exactly when `status` is `no_samples`. Never a zeroed node. */
  nodes_sampled: NodeSamplesInWindow[];
}

/** One `calls` row with the node samples overlapping its window. */
export interface CallNodeCorrelation extends CallRow {
  /**
   * Null when the CALL has no closed span (in flight, or an INVITE that never
   * produced a room). That is NOT `no_samples`: nothing was looked for, because
   * substituting "now" for a missing end would invent the span.
   */
  correlation: WindowCorrelation | null;
}

/** GET /api/nodes */
export interface NodeCorrelationResponse {
  /** The pad used for this request, readable even with no call to carry one. */
  pad_ms: number;
  /**
   * Whole `node_samples` row count. What separates "this deployment scrapes
   * nothing" from "this particular window had nothing in it", which a
   * `no_samples` status on its own cannot say.
   */
  samples_stored: number;
  calls: CallNodeCorrelation[];
}


/**
 * One test step of a load run, as GET /api/loadtest/runs returns it.
 *
 * Every measured field is nullable and null means NOT MEASURED, never zero. A
 * 0 in `peak_concurrency` describes a test that carried no calls, which is a
 * different claim from "the artifacts did not say", and a renderer that shows
 * both as 0 makes the second unfalsifiable.
 */
export interface LoadRunTest {
  id: number | null;
  run_id: string;
  name: string;
  sequence: number;
  started_at_ms: number | null;
  ended_at_ms: number | null;
  target_concurrency: number | null;
  peak_concurrency: number | null;
  attempted_calls: number | null;
  succeeded_calls: number | null;
  failed_calls: number | null;
  failed_timeout: number | null;
  failed_unexpected_sip: number | null;
  failed_transport_error: number | null;
  failed_parse_error: number | null;
  failed_scenario_error: number | null;
  failed_cancelled: number | null;
  peak_cpu_utilisation: number | null;
  peak_memory_utilisation: number | null;
  node_samples_in_window: number | null;
  rtp_packets_sent: number | null;
  rtp_packets_received: number | null;
  created_at_ms: number;
}

/**
 * One imported load run. `data_provenance` is derived server-side from whether
 * the run holds a real artifact checksum, so nothing can assert measured-ness
 * without the artifact behind it.
 */
export interface LoadRun {
  id: string;
  project: string;
  label: string | null;
  tool: string | null;
  tool_version: string | null;
  artifact_schema_version: string | null;
  started_at_ms: number | null;
  ended_at_ms: number | null;
  artifact_sha256: string | null;
  artifact_source: string | null;
  notes: string | null;
  created_at_ms: number;
  data_provenance: 'measured' | 'synthetic';
  tests: LoadRunTest[];
}

export interface LoadRunsResponse {
  runs: LoadRun[];
}
