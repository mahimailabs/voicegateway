// Google, GitHub and Auth0 sign-in (phase 06h, decisions 0035 and 0036), from web's /auth routes
// like sign-in, so not in docs/api/openapi.yaml. A provider is off unless the server says it is on.

export type ProviderName = "google" | "github" | "auth0"

export type Providers = Record<ProviderName, boolean>

export const NO_PROVIDERS: Providers = { google: false, github: false, auth0: false }

/** Which provider buttons to show. Any failure reads as none, so the password form still works. */
export async function fetchProviders(signal?: AbortSignal): Promise<Providers> {
  try {
    const res = await fetch("/auth/providers", { credentials: "same-origin", signal })
    if (!res.ok) return NO_PROVIDERS
    const body = (await res.json()) as Partial<Providers>
    return { google: body.google === true, github: body.github === true, auth0: body.auth0 === true }
  } catch {
    return NO_PROVIDERS
  }
}

export type StartResult = { kind: "redirect"; url: string } | { kind: "https-required" } | { kind: "failed" }

/**
 * Starts a provider sign-in: the server keeps its state in the session and answers the provider's
 * URL to go to. An invitation's token goes in a header, never a URL.
 */
export async function startProvider(provider: ProviderName, invitationToken?: string): Promise<StartResult> {
  let res: Response
  try {
    const init: RequestInit = { credentials: "same-origin", headers: invitationToken ? { "X-Invitation-Token": invitationToken } : {} }
    // Literal paths, so the reader inventory (docs/api/readers.json) can list each.
    if (provider === "google") res = await fetch("/auth/oauth/google/start", init)
    else if (provider === "github") res = await fetch("/auth/oauth/github/start", init)
    else res = await fetch("/auth/oauth/auth0/start", init)
  } catch {
    return { kind: "failed" }
  }
  const body = (await res.json().catch(() => ({}))) as { url?: string; code?: string }
  if (res.ok && typeof body.url === "string") return { kind: "redirect", url: body.url }
  if (res.status === 400 && body.code === "https_required") return { kind: "https-required" }
  return { kind: "failed" }
}

/** The one line every refused provider sign-in shows, whatever the cause. */
export const PROVIDER_REFUSED = "We could not sign you in with that account. Use the email you were invited with."

/** Takes ?error=oauth_refused from the address, once, and drops it. */
export function readProviderRefusal(): boolean {
  const params = new URLSearchParams(window.location.search)
  if (params.get("error") !== "oauth_refused") return false
  // The callback lands on /sign-in, which is not a console page: the console's root signs in.
  window.history.replaceState(null, "", window.location.pathname === "/sign-in" ? "/" : window.location.pathname)
  return true
}
