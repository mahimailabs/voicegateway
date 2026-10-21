import type { ConnectionSource, ProjectConnection } from "@/lib/api-types"

// The state of each first-call setup step and the fix for each refusal, from getProjectConnection
// alone; the console infers nothing.

export type Reason = NonNullable<ConnectionSource["reason"]>
export type Source = "webhook" | "agent" | "sdk"
export type Refusal = { at: string; reason: Reason | null }
export type StepState =
  | { state: "waiting" }
  | { state: "connected"; at: string; refused: Refusal | null }
  | { state: "error"; at: string; reason: Reason | null }

// A refusal after an accepted request is a warning, not an error, while that accepted request is at
// most this old: one stray request must not hide that the source works.
export const RECENT_MS = 10 * 60_000

const time = (iso: string | null) => (iso === null ? null : Date.parse(iso))

// Waiting when nothing was accepted or refused. Connected when the last accepted request is at least
// as new as the last refused one, or older but within RECENT_MS of now (with the refusal as a
// warning). Error when something was refused and nothing was accepted, or the last accepted request
// is older than the refusal and than RECENT_MS.
export function sourceState(s: ConnectionSource, now: number): StepState {
  const ok = time(s.lastAcceptedAt)
  const bad = time(s.lastRejectedAt)
  if (ok === null && bad === null) return { state: "waiting" }
  if (ok !== null && (bad === null || ok >= bad)) return { state: "connected", at: s.lastAcceptedAt!, refused: null }
  if (ok !== null && now - ok <= RECENT_MS) return { state: "connected", at: s.lastAcceptedAt!, refused: { at: s.lastRejectedAt!, reason: s.reason } }
  return { state: "error", at: s.lastRejectedAt!, reason: s.reason }
}

// Every source, the optional SDK included. A source never used is Waiting, which is neither an error
// nor a warning, so an SDK nobody attached never shows Error or keeps the panel polling; an SDK that
// was used and then refused counts like any other source.
const sources = (c: ProjectConnection) => [c.webhook, c.agent, c.sdk]

export const anyError = (c: ProjectConnection, now: number) => sources(c).some((s) => sourceState(s, now).state === "error")

const anyWarning = (c: ProjectConnection, now: number) =>
  sources(c).some((s) => {
    const st = sourceState(s, now)
    return st.state === "connected" && st.refused !== null
  })

// Ask again until the first call is stored and no source is in Error or shows a refusal warning, so a
// rotated key or a used-up allowance shows without a reload, and a warning turns into Error once its
// accept is more than RECENT_MS old.
export const keepPolling = (c: ProjectConnection | undefined, now: number) => !c || !c.firstCall || anyError(c, now) || anyWarning(c, now)

export type Fix = {
  title: string
  fix: string
  // key_mismatch: the key the token named (null when it was not a well-formed LiveKit key) and the
  // key this project expects. API keys are not secret; secrets are never shown.
  keys?: { used: string | null; expected: string | null }
  // plan_cap and plan_inactive: offer the same "Request more capacity" link as the usage allowance.
  capacity?: boolean
}

// What went wrong and how to fix it, per reason code. The SDK signs each batch with the agent's own
// LiveKit key and secret and posts to VOICEGATEWAY_URL, which is the Agent upload URL, so its fixes
// name those.
export function fixText(source: Source, s: ConnectionSource): Fix {
  const agent = source === "agent"
  const sdk = source === "sdk"
  switch (s.reason) {
    case "token_missing":
      if (sdk) return { title: "The SDK batch carried no LiveKit token", fix: "The SDK signs with the agent's LIVEKIT_API_KEY and LIVEKIT_API_SECRET. Check that both are set where the agent calls attach()." }
      return agent
        ? { title: "The upload carried no LiveKit token", fix: "Check that LIVEKIT_API_KEY and LIVEKIT_API_SECRET are set in the agent's environment." }
        : { title: "The webhook carried no LiveKit token", fix: "Add the webhook URL in LiveKit, so LiveKit signs each webhook it sends." }
    case "key_mismatch":
      if (sdk) return { title: "The token named another API key", fix: "Use this project's LiveKit API key in the agent, or set VOICEGATEWAY_URL to the Agent upload URL of the project that key belongs to.", keys: { used: s.usedKey, expected: s.expectedKey } }
      return { title: "The token named another API key", fix: "Use this project's LiveKit API key, or point the agent and webhook at the URLs of the project that key belongs to.", keys: { used: s.usedKey, expected: s.expectedKey } }
    case "signature_invalid":
      return {
        title: "The token did not verify",
        fix: `The secret is wrong, the token expired, or it is not a LiveKit token. ${
          sdk
            ? "The SDK signs with the agent's LIVEKIT_API_SECRET; set it to this project's LiveKit secret."
            : agent
              ? "Set LIVEKIT_API_SECRET in the agent to this project's LiveKit secret."
              : "The secret VoiceGateway stores for this project must match the LiveKit project that signs the webhook."
        }`,
      }
    case "grant_missing":
      if (sdk) return { title: "The SDK token lacks the observability grant", fix: "Upgrade the voicegateway Python package, which adds the grant to every batch it signs." }
      return { title: "The upload token lacks the observability grant", fix: "Upgrade the agent to Python livekit-agents 1.8.6 or later, or Node @livekit/agents 1.8.0 or later." }
    case "body_changed":
      return { title: "The webhook body was changed on the way", fix: "Something between LiveKit and VoiceGateway, such as a proxy, rewrote the body. Send it unmodified." }
    case "plan_inactive":
      return { title: "Telemetry ingestion paused", fix: "This workspace's plan is not active. Existing data stays readable and your voice calls continue.", capacity: true }
    case "plan_cap":
      return { title: "Telemetry ingestion paused", fix: "This workspace's allowance is used. Existing data stays readable and your voice calls continue.", capacity: true }
    case "too_large":
      if (sdk) return { title: "The request was too large", fix: "An SDK batch holds at most 1,000 events and 1 MiB. Open an issue with the voicegateway package version." }
      return agent
        ? { title: "The request was too large", fix: "The session report, recording included, is over the size limit. Open an issue with the agent's livekit-agents version." }
        : { title: "The request was too large", fix: "LiveKit webhooks are small, so check that no proxy between LiveKit and VoiceGateway adds to the body." }
    default:
      return { title: "The last request was refused", fix: "No reason was recorded." }
  }
}
