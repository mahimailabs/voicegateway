// The signed-in session, from web's /auth routes (decision 0027, phase 06a). These are not console
// reads, so they are not in docs/api/openapi.yaml.

export type Session = { email: string; organisations: { id: string; role: string }[] }

export type SessionRead = { signedIn: true; session: Session } | { signedIn: false; needsAdmin: boolean }

let current: Session | null = null

/** The session read at start, for the account menu; null with the mock or when signed out. */
export const currentSession = () => current

/** Records the session read at start. */
export const setCurrentSession = (s: Session | null) => {
  current = s
}

/** Reads who is signed in. No answer at all is an error the caller shows as such. */
export async function fetchSession(): Promise<SessionRead> {
  const res = await fetch("/auth/session", { credentials: "same-origin" })
  if (res.status === 401) {
    const body = (await res.json().catch(() => ({}))) as { needsAdmin?: boolean }
    return { signedIn: false, needsAdmin: body.needsAdmin === true }
  }
  if (!res.ok) throw new Error(`session read failed: ${res.status}`)
  return { signedIn: true, session: (await res.json()) as Session }
}

export type SignInResult =
  | { kind: "signed-in" }
  | { kind: "wrong" }
  | { kind: "limited"; retryAfterSeconds: number | null }
  | { kind: "https-required" }
  | { kind: "failed" }

/** Signs in. Every outcome is a result; nothing here throws for an answer the server gave. */
export async function signIn(email: string, password: string): Promise<SignInResult> {
  let res: Response
  try {
    res = await fetch("/auth/sign-in", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    })
  } catch {
    return { kind: "failed" }
  }
  if (res.status === 204) return { kind: "signed-in" }
  if (res.status === 401) return { kind: "wrong" }
  if (res.status === 429) {
    const after = Number(res.headers.get("Retry-After"))
    return { kind: "limited", retryAfterSeconds: Number.isFinite(after) && after > 0 ? after : null }
  }
  const body = (await res.json().catch(() => ({}))) as { code?: string }
  if (res.status === 400 && body.code === "https_required") return { kind: "https-required" }
  return { kind: "failed" }
}

/** Signs out, ending the session on the server. */
export async function signOut(): Promise<void> {
  await fetch("/auth/sign-out", { method: "POST", credentials: "same-origin" })
}
