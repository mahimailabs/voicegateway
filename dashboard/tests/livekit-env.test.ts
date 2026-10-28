import { expect, test } from "vitest"
import { filledText, namesLiveKit, parseLiveKitEnv } from "../src/lib/livekit-env.ts"

// Made-up values; none reaches a LiveKit server.
const all = { url: "wss://example-project.livekit.cloud", apiKey: "APIexample123", apiSecret: "secretexample" }

test.each([
  ["LiveKit Cloud's block", "LIVEKIT_URL=wss://example-project.livekit.cloud\nLIVEKIT_API_KEY=APIexample123\nLIVEKIT_API_SECRET=secretexample"],
  ["export prefixes", "export LIVEKIT_URL=wss://example-project.livekit.cloud\nexport LIVEKIT_API_KEY=APIexample123\nexport  LIVEKIT_API_SECRET=secretexample"],
  ["double and single quotes", `LIVEKIT_URL="wss://example-project.livekit.cloud"\nLIVEKIT_API_KEY='APIexample123'\nLIVEKIT_API_SECRET="secretexample"`],
  ["CRLF line ends", "LIVEKIT_URL=wss://example-project.livekit.cloud\r\nLIVEKIT_API_KEY=APIexample123\r\nLIVEKIT_API_SECRET=secretexample\r\n"],
  ["blank lines, comments and unknown keys", "# LiveKit\n\n  # copied today\nOTHER=1\nLIVEKIT_URL=wss://example-project.livekit.cloud\n\nLIVEKIT_API_KEY = APIexample123 # the key\nLIVEKIT_API_SECRET=secretexample\nDEEPGRAM_API_KEY=x\n"],
])("%s parse to the three values", (_name, text) => {
  expect(parseLiveKitEnv(text)).toEqual(all)
})

test("a partial paste fills only what it names", () => {
  const env = parseLiveKitEnv("LIVEKIT_API_KEY=APIexample123\nLIVEKIT_API_SECRET=secretexample")
  expect(env).toEqual({ apiKey: "APIexample123", apiSecret: "secretexample" })
  expect(filledText(env)).toBe("Filled key and secret from the pasted variables.")
  expect(filledText(parseLiveKitEnv("LIVEKIT_URL=wss://example-project.livekit.cloud"))).toBe("Filled URL from the pasted variables.")
})

test("a quoted value keeps its # and spaces; an empty value is left out", () => {
  expect(parseLiveKitEnv(`LIVEKIT_API_SECRET="secret # example"\nLIVEKIT_API_KEY=`)).toEqual({ apiSecret: "secret # example" })
})

test("text with none of the variables names nothing", () => {
  for (const text of ["", "APIexample123", "OTHER=1\n# LIVEKIT_API_KEY=APIexample123", "LIVEKIT_API_KEY"]) expect(namesLiveKit(parseLiveKitEnv(text))).toBe(false)
  expect(filledText(all)).toBe("Filled URL, key and secret from the pasted variables.")
})
