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

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
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
    throw new Error(detail ?? `HTTP ${res.status}`);
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
  DeadAirEvent,
  MetricsAggregate,
  ReplayResponse,
  RetentionWindow,
  TurnRow,
} from './types';

export function fetchMetricsSummary(
  options: { project?: string; days?: number } = {},
): Promise<MetricsAggregate> {
  const params = new URLSearchParams();
  if (options.project) params.set('project', options.project);
  if (options.days !== undefined) params.set('days', String(options.days));
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
