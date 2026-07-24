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
  /** Last-seen model per modality, from /api/agents; a modality is null when unseen. */
  models?: { stt: string | null; llm: string | null; tts: string | null };
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

/** Per-check result inside a DiagnosticRun's results map. */
export interface DiagnosticCheckResult {
  ok: boolean;
  result?: unknown;
  error?: string;
}

/** One diagnostic run record from the backend. */
export interface DiagnosticRun {
  run_id: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  checks: string[];
  config: Record<string, unknown>;
  results: { checks: Record<string, DiagnosticCheckResult>; verdict: string } | null;
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

