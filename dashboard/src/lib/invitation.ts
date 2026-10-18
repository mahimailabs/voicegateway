// The accept page's two calls (phase 06d), outside /api/v1 like sign-in. The token goes in a
// header and a body, never a URL, so no log holds it.

export type PendingInvitation = { kind: "invite" | "reset"; organisationName: string; role: "Admin" | "Member" | "Viewer" | null; email: string }

export type LookResult = { kind: "pending"; invitation: PendingInvitation } | { kind: "invalid" } | { kind: "failed" }

/** Reads what a link is for. Every link that does not work reads the same: invalid. */
export async function lookInvitation(token: string): Promise<LookResult> {
  let res: Response
  try {
    res = await fetch("/auth/invitation", { credentials: "same-origin", headers: { "X-Invitation-Token": token } })
  } catch {
    return { kind: "failed" }
  }
  if (res.status === 200) return { kind: "pending", invitation: (await res.json()) as PendingInvitation }
  if (res.status === 404) return { kind: "invalid" }
  return { kind: "failed" }
}

export type AcceptResult =
  | { kind: "accepted" }
  | { kind: "invalid" }
  | { kind: "wrong" }
  | { kind: "too-short" }
  | { kind: "limited"; retryAfterSeconds: number | null }
  | { kind: "https-required" }
  | { kind: "failed" }

/** Uses a link with a password. Every outcome is a result; nothing here throws for an answer. */
export async function acceptInvitation(token: string, password: string): Promise<AcceptResult> {
  let res: Response
  try {
    res = await fetch("/auth/invitation/accept", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, password }),
    })
  } catch {
    return { kind: "failed" }
  }
  if (res.status === 204) return { kind: "accepted" }
  if (res.status === 401) return { kind: "wrong" }
  if (res.status === 404) return { kind: "invalid" }
  if (res.status === 429) {
    const after = Number(res.headers.get("Retry-After"))
    return { kind: "limited", retryAfterSeconds: Number.isFinite(after) && after > 0 ? after : null }
  }
  const body = (await res.json().catch(() => ({}))) as { code?: string }
  if (res.status === 400 && body.code === "https_required") return { kind: "https-required" }
  if (res.status === 400) return { kind: "too-short" }
  return { kind: "failed" }
}
