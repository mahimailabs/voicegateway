import type { ConnectionSource } from "@/lib/api-types"

// The state of each first-call setup step and the fix for each refusal, from getProjectConnection
// alone; the console infers nothing.

export type Reason = NonNullable<ConnectionSource["reason"]>
export type Source = "webhook" | "agent"
export type StepState = { state: "waiting" } | { state: "connected"; at: string } | { state: "error"; at: string; reason: Reason | null }

const time = (iso: string | null) => (iso === null ? null : Date.parse(iso))

// Connected when the last accepted request is newer than the last refused one, Error when the last
// refused one is newer, Waiting otherwise (nothing seen yet, or both at the same instant).
export function sourceState(s: ConnectionSource): StepState {
  const ok = time(s.lastAcceptedAt)
  const bad = time(s.lastRejectedAt)
  if (ok !== null && (bad === null || ok > bad)) return { state: "connected", at: s.lastAcceptedAt! }
  if (bad !== null && (ok === null || bad > ok)) return { state: "error", at: s.lastRejectedAt!, reason: s.reason }
  return { state: "waiting" }
}

const key = (k: string | null) => k ?? "unknown"

// What went wrong and how to fix it, per reason code. API keys are not secret; secrets are never shown.
export function fixText(source: Source, s: ConnectionSource): { title: string; fix: string } {
  switch (s.reason) {
    case "token_missing":
      return source === "agent"
        ? { title: "The upload carried no LiveKit token", fix: "Check that LIVEKIT_API_KEY and LIVEKIT_API_SECRET are set in the agent's environment." }
        : { title: "The webhook carried no LiveKit token", fix: "Add the webhook URL in LiveKit, so LiveKit signs each webhook it sends." }
    case "key_mismatch":
      return { title: `Used key ${key(s.usedKey)}, this project expects ${key(s.expectedKey)}`, fix: "Use this project's LiveKit API key, or point the agent and webhook at the URLs of the project that key belongs to." }
    case "signature_invalid":
      return { title: "The token's signature did not verify", fix: "The key is right but the secret is wrong, or the token expired. Set this project's LiveKit API secret." }
    case "grant_missing":
      return { title: "The upload token lacks the observability grant", fix: "Upgrade livekit-agents to 1.8.6 or later. For Node agents, use a release that honours LIVEKIT_OBSERVABILITY_URL." }
    case "body_changed":
      return { title: "The webhook body was changed on the way", fix: "Something between LiveKit and VoiceGateway, such as a proxy, rewrote the body. Send it unmodified." }
    case "plan_inactive":
      return { title: "The workspace plan is inactive or expired", fix: "Open billing for this workspace and renew the plan." }
    case "plan_cap":
      return { title: "The plan's minutes are used up", fix: "Open billing for this workspace to add minutes." }
    case "too_large":
      return { title: "The request was too large", fix: "The request exceeded the size limit and was refused." }
    default:
      return { title: "The last request was refused", fix: "No reason was recorded." }
  }
}
