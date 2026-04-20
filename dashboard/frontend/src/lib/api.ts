const API_BASE = '';
const TOKEN_KEY = 'voicegw_token';

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

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (init?.body != null && headers['Content-Type'] == null) {
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
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
