import { DEMO_MODE } from './demo';

const API_BASE = '';
const TOKEN_KEY = 'voicegw_token';

/** Dispatched on every 401/403 from fetchJson so the app can show the login gate. */
export const AUTH_REQUIRED_EVENT = 'voicegw:auth-required';

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // storage unavailable (private mode, etc.) — caller sees this via failing fetches
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // ignore
  }
}

function buildHeaders(init?: RequestInit): Headers {
  // Using the Headers class preserves case-insensitivity — callers passing
  // either a plain object, an existing Headers instance, or an array of
  // tuples all work the same way.
  const headers = new Headers(init?.headers);
  const token = getToken();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (init?.body != null && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return headers;
}

/**
 * A non-2xx response, carrying what the server said about it.
 *
 * Extends Error so every existing `catch (err) { err instanceof Error }` site
 * keeps working unchanged; callers that care about the refusal (a rate limit
 * that names when to come back) can read `status` and `retryAfterSeconds`
 * instead of re-parsing the message.
 */
export class ApiError extends Error {
  readonly status: number;
  /** Seconds from Retry-After, when the server sent a parseable one. */
  readonly retryAfterSeconds: number | null;

  constructor(message: string, status: number, retryAfterSeconds: number | null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

function parseRetryAfter(res: Response): number | null {
  // Only the delta-seconds form is honoured. The HTTP-date form is legal but
  // this API never sends it, and guessing at a date against a client clock that
  // may be skewed would produce a countdown that is confidently wrong.
  const raw = res.headers.get('Retry-After');
  if (!raw) return null;
  const seconds = Number(raw.trim());
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  if (DEMO_MODE) {
    // No backend in the demo: serve seeded fixtures. The dynamic import keeps the
    // fixtures out of the real dashboard bundle (this whole branch is dead code
    // when DEMO_MODE folds to false at build time).
    const { demoFetch } = await import('./demoFixtures');
    return demoFetch<T>(path, init);
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: buildHeaders(init),
  });

  if (res.status === 401 || res.status === 403) {
    // The session is no longer authorized — wipe the stored token and
    // ask the app to show the login gate. Still throw so the caller's
    // await path unwinds normally instead of trying to parse the body.
    clearToken();
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT));
    }
    throw new Error(`HTTP ${res.status}`);
  }

  if (!res.ok) {
    const detail = await extractErrorDetail(res);
    throw new ApiError(
      detail ?? `HTTP ${res.status}`,
      res.status,
      parseRetryAfter(res),
    );
  }
  return res.json() as Promise<T>;
}

async function extractErrorDetail(res: Response): Promise<string | null> {
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') return body.detail;
    if (typeof body?.error?.message === 'string') return body.error.message;
    return null;
  } catch {
    return null;
  }
}

// ----------------------------------------------------------------------
// v0.2.0 voice-conversation metrics typed fetchers (REQ-VG-METRICS-001..006).
// Thin wrappers around fetchJson<T>() that pin the response type and the
// path string. Callers should prefer these over raw `fetchJson` calls so
// path / type drift is caught at compile time.
// ----------------------------------------------------------------------

import type {
  AgentProbeResult,
  AgentRow,
  AgentsResponse,
  ApiKey,
  CallsResponse,
  CorrelationRate,
  CreatedApiKey,
  DeadAirEvent,
  DiagnosticRun,
  DiagnosticsCreds,
  LogoUploadResponse,
  MetricsAggregate,
  ProjectBranding,
  ProjectBrandingResponse,
  ReplayResponse,
  RetentionWindow,
  ServerOverview,
  TenantFilter,
  TurnRow,
} from './types';

/**
 * Append the tenant filter to a URLSearchParams instance per the v0.4.0
 * convention. ``null`` is "no filter" (param not set); ``""`` is the
 * unattributed bucket (param set to empty string); any other value is
 * that exact tenant. Matches the backend's ``tenant`` query parsing on
 * /api/costs, /api/latency, /api/sessions, and /api/metrics.
 */
export function appendTenantParam(
  params: URLSearchParams,
  tenant: TenantFilter | undefined,
): void {
  if (tenant === null || tenant === undefined) return;
  params.set('tenant', tenant);
}

export function fetchMetricsSummary(
  options: { project?: string; days?: number; tenant?: TenantFilter } = {},
): Promise<MetricsAggregate> {
  const params = new URLSearchParams();
  if (options.project) params.set('project', options.project);
  if (options.days !== undefined) params.set('days', String(options.days));
  appendTenantParam(params, options.tenant);
  const query = params.toString();
  return fetchJson<MetricsAggregate>(
    query ? `/api/metrics?${query}` : '/api/metrics',
  );
}

export function fetchSessionTurns(
  sessionId: string,
): Promise<{ session_id: string; turns: TurnRow[] }> {
  return fetchJson(`/api/sessions/${encodeURIComponent(sessionId)}/turns`);
}

export function fetchSessionDeadAir(
  sessionId: string,
): Promise<{ session_id: string; events: DeadAirEvent[] }> {
  return fetchJson(`/api/sessions/${encodeURIComponent(sessionId)}/dead_air`);
}

// ----------------------------------------------------------------------
// v0.3.0 conversation-replay typed fetchers (REQ-VG-REPLAY-001..006).
// Same wrap-fetchJson pattern as the v0.2.0 metrics fetchers above.
// ----------------------------------------------------------------------

export function fetchSessionReplay(
  sessionId: string,
): Promise<ReplayResponse> {
  return fetchJson<ReplayResponse>(
    `/api/sessions/${encodeURIComponent(sessionId)}/replay`,
  );
}

export function deleteSessionReplay(
  sessionId: string,
): Promise<{ session_id: string; deleted_rows: number }> {
  return fetchJson(
    `/api/sessions/${encodeURIComponent(sessionId)}/replay`,
    { method: 'DELETE' },
  );
}

export function fetchReplayStorage(): Promise<{
  total_replay_size_bytes: number;
  by_project: Array<{ project: string; replay_size_bytes: number }>;
}> {
  return fetchJson('/api/replay/storage');
}

export function updateReplayRetention(
  projectId: string,
  retentionDays: number,
): Promise<RetentionWindow> {
  return fetchJson<RetentionWindow>(
    `/api/projects/${encodeURIComponent(projectId)}/replay/retention`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ retention_days: retentionDays }),
    },
  );
}

// ----------------------------------------------------------------------
// Phase 2 fleet: per-agent typed fetchers (mirror tenant). Pages set the
// `agent` param inline via useAgentFilter(), matching how they set `tenant`.
// ----------------------------------------------------------------------

export function fetchAgents(
  options: { limit?: number; q?: string } = {},
): Promise<AgentsResponse> {
  const params = new URLSearchParams();
  if (options.limit !== undefined) params.set('limit', String(options.limit));
  if (options.q !== undefined && options.q.length > 0) params.set('q', options.q);
  const query = params.toString();
  return fetchJson<AgentsResponse>(query ? `/api/agents?${query}` : '/api/agents');
}

export function fetchAgent(agentId: string): Promise<AgentRow> {
  return fetchJson<AgentRow>(`/api/agents/${encodeURIComponent(agentId)}`);
}

/**
 * How long the UI keeps the play button disabled after a probe.
 *
 * Mirrors PROBE_COOLDOWN_SECONDS in server/api/dashboard/agents.py. The server
 * is authoritative (it answers 429 with Retry-After if this drifts); this only
 * spares the operator a round trip that was always going to be refused.
 */
export const PROBE_COOLDOWN_SECONDS = 30;

/**
 * Place one real call to this agent and return what it measured.
 *
 * Every press is billed traffic against the agent's real providers, so the
 * endpoint is admin-scoped and rate limited: 409 while one is in flight, 429
 * inside the cooldown. Both come back as a thrown ApiError carrying the
 * server's detail string, plus Retry-After on the 429 so the caller can render
 * the wait the server is actually enforcing rather than re-offering a press it
 * has already decided to refuse.
 */
export function probeAgent(agentId: string): Promise<AgentProbeResult> {
  return fetchJson<AgentProbeResult>(
    `/api/agents/${encodeURIComponent(agentId)}/probe`,
    { method: 'POST' },
  );
}

export function fetchApiKeys(
  options: { includeRevoked?: boolean } = {},
): Promise<{ keys: ApiKey[] }> {
  const params = new URLSearchParams();
  if (options.includeRevoked !== undefined) {
    params.set('include_revoked', options.includeRevoked ? 'true' : 'false');
  }
  const query = params.toString();
  return fetchJson<{ keys: ApiKey[] }>(
    query ? `/api/api_keys?${query}` : '/api/api_keys',
  );
}

export function createApiKey(body: {
  name: string;
  tenant_id?: string | null;
  issued_by?: string | null;
}): Promise<CreatedApiKey> {
  return fetchJson<CreatedApiKey>('/api/api_keys', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function revokeApiKey(
  keyId: number,
): Promise<{ id: number; revoked: true; row: ApiKey }> {
  return fetchJson(`/api/api_keys/${keyId}/revoke`, { method: 'POST' });
}

// ----------------------------------------------------------------------
// v0.5.0 cross-modality routing + white-label branding typed fetchers
// (REQ-VG-ROUTE-001..004).
// ----------------------------------------------------------------------

export function fetchProjectBranding(
  projectId: string,
): Promise<ProjectBrandingResponse> {
  return fetchJson<ProjectBrandingResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/branding`,
  );
}

export function updateProjectBranding(
  projectId: string,
  branding: ProjectBranding,
): Promise<ProjectBrandingResponse> {
  return fetchJson<ProjectBrandingResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/branding`,
    {
      method: 'POST',
      body: JSON.stringify(branding),
    },
  );
}

// ---------------------------------------------------------------------------
// Diagnostics typed fetchers (REQ-VG-DIAG-001).
// ---------------------------------------------------------------------------

export function fetchDiagnosticsCreds(): Promise<DiagnosticsCreds> {
  return fetchJson<DiagnosticsCreds>('/api/diagnostics/creds');
}

export function fetchDiagnosticsRuns(): Promise<DiagnosticRun[]> {
  return fetchJson<DiagnosticRun[]>('/api/diagnostics/runs');
}

export function fetchDiagnosticsRun(runId: string): Promise<DiagnosticRun> {
  return fetchJson<DiagnosticRun>(`/api/diagnostics/runs/${encodeURIComponent(runId)}`);
}

export function createDiagnosticsRun(body: {
  checks: string[];
  config?: Record<string, unknown>;
}): Promise<{ run_id: string; status: string }> {
  return fetchJson<{ run_id: string; status: string }>('/api/diagnostics/runs', {
    method: 'POST',
    body: JSON.stringify({ checks: body.checks, config: body.config ?? {} }),
  });
}

// ---------------------------------------------------------------------------
// Calls: the recorded call (SIP -> SFU -> dispatch -> agent) with its legs.
//
// Read-only and polled, never pushed: the dashboard has zero WebSocket by
// design, and this surface is no exception.
// ---------------------------------------------------------------------------

export function fetchCalls(
  options: { limit?: number } = {},
): Promise<CallsResponse> {
  const params = new URLSearchParams();
  if (options.limit !== undefined) params.set('limit', String(options.limit));
  const query = params.toString();
  return fetchJson<CallsResponse>(query ? `/api/calls?${query}` : '/api/calls');
}

// ---------------------------------------------------------------------------
// Correlation: how often the sessions <-> calls join actually resolves.
//
// Takes no argument on purpose. The endpoint publishes the deployment-wide
// number and its warn threshold; the browser neither scopes it nor recomputes
// it.
// ---------------------------------------------------------------------------

export function fetchCorrelationRate(): Promise<CorrelationRate> {
  return fetchJson<CorrelationRate>('/api/correlation');
}

// ---------------------------------------------------------------------------
// Server page: live LiveKit topology + fleet, cost-annotated (read-only).
// ---------------------------------------------------------------------------

export function fetchServerOverview(): Promise<ServerOverview> {
  return fetchJson<ServerOverview>('/api/server/overview');
}

/**
 * Multipart logo upload. Uses raw ``fetch`` because ``fetchJson``
 * always sets ``Content-Type: application/json`` when a body is
 * provided; multipart needs the browser to set the boundary.
 *
 * Returns the served URL the operator can plug into
 * ``ProjectBranding.logo_url`` on the next branding POST.
 */
export async function uploadBrandingLogo(
  projectId: string,
  file: File,
): Promise<LogoUploadResponse> {
  if (DEMO_MODE) throw new Error('This is a read-only demo.');
  const fd = new FormData();
  fd.append('file', file);
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/branding/logo`,
    { method: 'POST', body: fd, headers },
  );
  if (res.status === 401 || res.status === 403) {
    clearToken();
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT));
    }
    throw new Error(`HTTP ${res.status}`);
  }
  if (!res.ok) {
    let detail: string | null = null;
    try {
      const body = await res.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      // fall through
    }
    throw new Error(detail ?? `HTTP ${res.status}`);
  }
  return (await res.json()) as LogoUploadResponse;
}

