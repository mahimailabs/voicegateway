import type { SentryClientConfig } from "@/lib/monitoring"

// What web tells the console at runtime (GET /auth/client-config), from web's /auth routes like
// sign-in, so not in docs/api/openapi.yaml. Each part is off unless the server turns it on.

export type ClientConfig = { sentry: SentryClientConfig | null }

const OFF: ClientConfig = { sentry: null }

/** Any failure reads as everything off. */
export async function fetchClientConfig(): Promise<ClientConfig> {
  try {
    const res = await fetch("/auth/client-config", { credentials: "same-origin" })
    if (!res.ok) return OFF
    const body = (await res.json()) as Partial<ClientConfig>
    const s = body.sentry
    const sentry = s && typeof s.dsn === "string" && s.dsn !== "" ? { dsn: s.dsn, environment: String(s.environment ?? ""), tracesSampleRate: Number(s.tracesSampleRate) || 0 } : null
    return { sentry }
  } catch {
    return OFF
  }
}
