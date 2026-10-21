// Reads the "Environment variables" block LiveKit Cloud copies when it generates an API key:
//
//   LIVEKIT_URL=wss://<project>.livekit.cloud
//   LIVEKIT_API_KEY=API...
//   LIVEKIT_API_SECRET=...
//
// Built here because no library fits (checked 2026-10-21):
// - dotenv 18.0.8: parse ships only in dist/index.cjs, which requires fs, os, path and url at load;
//   there is no lib/main subpath any more, so it is not browser-safe without Node polyfills.
// - envfile 7.1.0: browser-safe, but takes no "export " prefix and strips every quote character
//   anywhere in a value, which would corrupt a secret; last published 2024-03.
// - parse-dotenv 2.1.0, dotenv-parse 1.0.1, dotenv-parser 0.0.5: unmaintained since 2022, and the
//   last depends on dotenv itself.
// - @std/dotenv: published on JSR only, not on the npm registry.
//
// Rules: one KEY=VALUE per line; an optional leading "export "; a value in single or double quotes
// is taken as written between them; an unquoted value ends at " #". CRLF, blank lines and # comment
// lines are skipped. Keys other than the three are ignored, so a whole .env file can be pasted.

export type LiveKitEnv = { url?: string; apiKey?: string; apiSecret?: string }

const fields = { LIVEKIT_URL: "url", LIVEKIT_API_KEY: "apiKey", LIVEKIT_API_SECRET: "apiSecret" } as const
type Name = keyof typeof fields
const line = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/

function value(raw: string): string {
  const v = raw.trim()
  const q = v[0]
  if ((q === '"' || q === "'") && v.length >= 2) {
    const end = v.indexOf(q, 1)
    if (end > 0) return v.slice(1, end)
  }
  const comment = v.search(/\s#/)
  return (comment >= 0 ? v.slice(0, comment) : v).trim()
}

/** The LiveKit URL, API key and secret named in text; a variable that is absent or empty is left out. */
export function parseLiveKitEnv(text: string): LiveKitEnv {
  const out: LiveKitEnv = {}
  for (const l of text.split(/\r?\n|\r/)) {
    if (/^\s*(#|$)/.test(l)) continue
    const m = l.match(line)
    if (!m || !(m[1] in fields)) continue
    const v = value(m[2])
    if (v !== "") out[fields[m[1] as Name]] = v
  }
  return out
}

/** True when the text named at least one of the three variables. */
export const namesLiveKit = (env: LiveKitEnv) => env.url !== undefined || env.apiKey !== undefined || env.apiSecret !== undefined

/** What a paste filled, as a sentence: "Filled URL, key and secret from the pasted variables." */
export function filledText(env: LiveKitEnv, include: ("url" | "apiKey" | "apiSecret")[] = ["url", "apiKey", "apiSecret"]): string {
  const names = { url: "URL", apiKey: "key", apiSecret: "secret" }
  const got = include.filter((k) => env[k] !== undefined).map((k) => names[k])
  const list = got.length <= 1 ? got.join("") : `${got.slice(0, -1).join(", ")} and ${got[got.length - 1]}`
  return `Filled ${list} from the pasted variables.`
}

export const NOTHING_PASTED = "No LIVEKIT_URL, LIVEKIT_API_KEY or LIVEKIT_API_SECRET in the pasted text."

// For the URL, key and secret inputs: pasting the whole block into any one of them fills all three.
// A paste that names none of the variables, such as a bare key, goes into the input as usual.
export const pasteLiveKit = (onParsed: (env: LiveKitEnv) => void) => (e: { clipboardData: { getData(format: string): string }; preventDefault(): void }) => {
  const env = parseLiveKitEnv(e.clipboardData.getData("text"))
  if (!namesLiveKit(env)) return
  e.preventDefault()
  onParsed(env)
}
