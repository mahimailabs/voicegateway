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
