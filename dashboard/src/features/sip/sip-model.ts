import type { Sip } from "@/lib/api-types"
import { type Path, hopIcons } from "./call-path"

// The SIP setup of a project as read from ListSIPInboundTrunk, ListSIPOutboundTrunk,
// ListSIPDispatchRule and ListPhoneNumbers, and what it means: which path each number takes, what is
// wrong with each trunk and rule, and the hop-by-hop path for a chosen number or outbound trunk.

// The contract's SIP read (getSip). test marks the hand-written layout example, which is not API data.
export type SipData = Sip & { test?: boolean }
export type Inbound = Sip["inbound"][number]
export type Outbound = Sip["outbound"][number]
export type Rule = Sip["rules"][number]

export type CallHint = { trunkId: string | null; twilio: boolean }

const docs = "https://docs.livekit.io"
export const enc = (e: string | null | undefined) => (e === "SIP_MEDIA_ENCRYPT_REQUIRE" ? "SRTP" : e === "SIP_MEDIA_ENCRYPT_ALLOW" ? "SRTP if offered" : "RTP")
export const transport = (t: string) => t.replace("SIP_TRANSPORT_", "")
// The trunk's host (no scheme, user or port) is the provider's domain or a subdomain of it.
const hostOf = (address: string) =>
  address
    .replace(/^sips?:/, "")
    .replace(/^[^@]*@/, "")
    .replace(/:\d+$/, "")
    .toLowerCase()
const onDomain = (host: string, domain: string) => host === domain || host.endsWith(`.${domain}`)
export const providerOf = (address: string) => {
  const host = hostOf(address)
  return onDomain(host, "telnyx.com") ? "Telnyx" : onDomain(host, "twilio.com") ? "Twilio" : onDomain(host, "plivo.com") ? "Plivo" : host.includes("wavix") ? "Wavix" : host.includes("didlogic") ? "didlogic" : host.includes("sinch") ? "Sinch" : null
}

export type Provider = { name: string; how: string; known: boolean }
export type Route = { key: string; number: string; wildcard: boolean; livekit: boolean; trunk: Inbound | null; rules: Rule[]; provider: Provider; duplicate: boolean }

const rulesFor = (d: SipData, t: Inbound) => d.rules.filter((r) => r.trunkIds.includes(t.id) || r.trunkIds.length === 0)

// LiveKit does not report who sends an inbound trunk's calls, so the provider is inferred and says how.
function providerFor(d: SipData, number: string | null, t: Inbound | null, hints: CallHint[]): Provider {
  if (!t) return { name: "LiveKit", how: "A LiveKit Phone Number: no third-party trunk.", known: true }
  const out = number ? d.outbound.find((o) => o.numbers.includes(number)) : undefined
  if (out) return { name: providerOf(out.address) ?? out.address, how: `The same number is on outbound trunk ${out.id}, which dials ${out.address}.`, known: true }
  const sibling = t.numbers.map((n) => [n, d.outbound.find((x) => x.numbers.includes(n))] as const).find(([, x]) => x)
  if (sibling) return { name: providerOf(sibling[1]!.address) ?? sibling[1]!.address, how: `Likely: another number on this trunk, ${sibling[0]}, is on outbound trunk ${sibling[1]!.id}, which dials ${sibling[1]!.address}.`, known: true }
  if (hints.some((h) => h.trunkId === t.id && h.twilio)) return { name: "Twilio", how: "A watched call through this trunk carried sip.twilio.* attributes.", known: true }
  return { name: "Unknown provider", how: "LiveKit does not report an inbound trunk's provider. No outbound trunk shares this number and no watched call has shown provider attributes.", known: false }
}

export function routes(d: SipData, hints: CallHint[]): Route[] {
  const count = new Map<string, number>()
  for (const t of d.inbound) for (const n of t.numbers) count.set(n, (count.get(n) ?? 0) + 1)
  const rows: Route[] = d.inbound.flatMap((t) =>
    (t.numbers.length ? t.numbers : [null]).map((n) => ({
      key: `${t.id}:${n ?? "*"}`,
      number: n ?? "Any number",
      wildcard: n === null,
      livekit: false,
      trunk: t,
      rules: rulesFor(d, t),
      provider: providerFor(d, n, t, hints),
      duplicate: n !== null && (count.get(n) ?? 0) > 1,
    })),
  )
  for (const ln of d.livekitNumbers)
    rows.push({ key: `lk:${ln.number}`, number: ln.number, wildcard: false, livekit: true, trunk: null, rules: d.rules.filter((r) => ln.ruleIds.includes(r.id)), provider: providerFor(d, ln.number, null, hints), duplicate: false })
  return rows
}

// Numbers that share a trunk and rules share one path, so the page draws paths, not numbers: a
// project with 200 numbers on 3 trunks has 3 diagrams, and a number is found by search.
export type InPath = { key: string; trunk: Inbound | null; rules: Rule[]; routes: Route[]; livekit: boolean; wildcard: boolean }
export function inPaths(rows: Route[]): InPath[] {
  const m = new Map<string, InPath>()
  for (const r of rows) {
    const key = `${r.trunk?.id ?? "livekit"}|${r.rules.map((x) => x.id).join(",")}`
    const p = m.get(key) ?? { key, trunk: r.trunk, rules: r.rules, routes: [], livekit: r.livekit, wildcard: false }
    p.routes.push(r)
    p.wildcard ||= r.wildcard
    m.set(key, p)
  }
  return [...m.values()]
}

// Every check is a rule from the LiveKit or provider docs, applied to the config as read.
export type Check = { severity: "high" | "medium" | "low"; title: string; where: string; why: string; fix: string; doc: string }
export function checks(d: SipData, rows: Route[]): Check[] {
  const out: Check[] = []
  for (const r of rows.filter((x) => !x.rules.length))
    out.push({ severity: "high", title: `Calls to ${r.number} are rejected`, where: r.trunk?.id ?? r.number, why: r.livekit ? "This LiveKit number has no dispatch rule assigned." : "No dispatch rule matches this trunk, so LiveKit has nowhere to put the caller.", fix: r.livekit ? "Assign a dispatch rule to the number." : "Create a dispatch rule bound to this trunk, or one with no trunk filter.", doc: `${docs}/telephony/accepting-calls/dispatch-rule/` })
  for (const t of d.inbound) {
    const rs = rulesFor(d, t)
    if (rs.filter((r) => r.pin === false).length > 1)
      out.push({ severity: "medium", title: `${rs.length} dispatch rules match one trunk with no PIN to tell them apart`, where: t.id, why: `Rules ${rs.map((r) => r.id).join(", ")} all match calls on this trunk. Multiple rules on a trunk are meant to differ by PIN.`, fix: "Bind each rule to its own trunk, or give each a PIN.", doc: `${docs}/telephony/accepting-calls/dispatch-rule/` })
  }
  for (const n of [...new Set(rows.filter((r) => r.duplicate).map((r) => r.number))])
    out.push({ severity: "medium", title: `${n} is on more than one inbound trunk`, where: rows.filter((r) => r.number === n).map((r) => r.trunk?.id).join(", "), why: "Which trunk, and so which rule and agent, takes the call is not obvious from the config.", fix: "Keep each number on one inbound trunk.", doc: `${docs}/telephony/accepting-calls/inbound-trunk/` })
  for (const t of d.inbound) {
    // Each must be reported off: an unreported setting is not evidence the trunk is open.
    if (t.auth === false && t.allowedAddresses === 0 && t.allowedNumbers === 0)
      out.push({ severity: "high", title: "Inbound trunk accepts calls from any source", where: t.id, why: "No IP allowlist, no auth and no allowed caller numbers. Anything that sends an INVITE for these numbers reaches the agent, and every such call spends model minutes.", fix: "Set allowed_addresses to your provider's signalling IPs (LiveKit enables the field on request), or auth if your provider supports it.", doc: `${docs}/telephony/accepting-calls/inbound-trunk/` })
    if (enc(t.media.encryption) === "RTP")
      out.push({ severity: "medium", title: "Inbound calls are not encrypted", where: t.id, why: "Media encryption is off, so phone audio travels as plain RTP between the provider and LiveKit.", fix: "Enable SRTP and TLS on the provider, then set media encryption to allow or require on the trunk.", doc: `${docs}/telephony/features/secure-trunking/` })
  }
  for (const t of d.outbound) {
    if (providerOf(t.address) === "Telnyx" && !t.headerNames.includes("X-Telnyx-Username"))
      out.push({ severity: "high", title: "Telnyx may match calls by IP, not by your credentials", where: t.id, why: "The trunk does not send X-Telnyx-Username. Without it Telnyx can skip the auth challenge and authenticate by source IP, and the matched connection could belong to another customer.", fix: "Add X-Telnyx-Username with your username to the outbound trunk's headers.", doc: `${docs}/telephony/start/providers/telnyx/` })
    if (transport(t.transport) !== "TLS")
      out.push({ severity: "medium", title: `Outbound signalling uses ${transport(t.transport)}, not TLS`, where: t.id, why: "Call setup, including caller and callee numbers, crosses the internet unencrypted.", fix: "Set transport to TLS on the trunk and enable TLS with the provider.", doc: `${docs}/telephony/features/secure-trunking/` })
    if (enc(t.media.encryption) === "RTP")
      out.push({ severity: "medium", title: "Outbound calls are not encrypted", where: t.id, why: "Media encryption is off, so audio to the provider is plain RTP.", fix: "Set media encryption to allow or require, with SRTP enabled on the provider.", doc: `${docs}/telephony/features/secure-trunking/` })
    if (!t.destinationCountry)
      out.push({ severity: "low", title: "Outbound calls are not region pinned", where: t.id, why: "No destination_country, so calls leave from the region nearest the API call. This matters only where regulation keeps calls in-country.", fix: "Set destination_country on the trunk if calls must stay in one country.", doc: `${docs}/telephony/features/region-pinning/` })
  }
  for (const r of d.rules) {
    if (r.type === "dispatchRuleIndividual" && r.hidePhoneNumber === false)
      out.push({ severity: "medium", title: "Room names contain the caller's phone number", where: r.id, why: "An individual rule names each room after the caller's number. Room names land in logs and traces and PII redaction does not remove them.", fix: "Route each call to a predetermined room ID, and set hide_phone_number on the rule.", doc: `${docs}/telephony/accepting-calls/dispatch-rule/` })
    if (!r.trunkIds.length && d.inbound.length > 1)
      out.push({ severity: "low", title: "Dispatch rule matches every trunk", where: r.id, why: "With no trunk_ids, calls on any trunk can use this rule.", fix: "Bind the rule to its trunk.", doc: `${docs}/telephony/accepting-calls/dispatch-rule/` })
    if (r.type === "dispatchRuleDirect" && r.pin === false)
      out.push({ severity: "medium", title: "Callers share one room", where: r.id, why: "A direct rule with no PIN puts unrelated callers in the same room, where they can hear each other.", fix: "Use an individual rule, or a callee rule with randomize on.", doc: `${docs}/telephony/accepting-calls/dispatch-rule/` })
    if (r.agents === 0)
      out.push({ severity: "low", title: "Dispatch rule sends no agent", where: r.id, why: "Callers wait in an empty room unless something else dispatches an agent.", fix: "Add the agent to the rule's room_config.", doc: `${docs}/telephony/accepting-calls/dispatch-rule/` })
  }
  const rank = { high: 0, medium: 1, low: 2 }
  return out.sort((a, b) => rank[a.severity] - rank[b.severity])
}

const issuesFor = (list: Check[], ids: (string | undefined)[]) => list.filter((c) => ids.some((id) => id && c.where.split(", ").includes(id))).map(({ title, severity }) => ({ title, severity }))

export function inboundPath(p: InPath, list: Check[]): Path {
  const t = p.trunk
  const rule = p.rules[0]
  const names = [...new Set(p.routes.map((r) => r.provider.name))]
  const known = p.routes.every((r) => r.provider.known)
  const nums = p.routes.filter((r) => !r.wildcard).map((r) => r.number)
  const media = t ? enc(t.media.encryption) : null
  return {
    hops: [
      { icon: hopIcons.phone, title: "Caller", sub: "phone network" },
      { icon: hopIcons.provider, title: names.length === 1 ? names[0] : `${names.length} providers`, sub: p.livekit ? "LiveKit number" : known ? "provider, inferred" : "not all reported", tags: [p.wildcard ? "any number" : nums.length === 1 ? nums[0] : `${nums.length} numbers`] },
      { icon: hopIcons.sip, title: "LiveKit SIP", sub: t?.id ?? "managed by LiveKit", tags: t ? [t.krisp === null ? "Krisp unknown" : t.krisp ? "Krisp on" : "Krisp off"] : undefined, issues: issuesFor(list, [t?.id, ...nums]) },
      {
        icon: hopIcons.room,
        title: !rule ? "No rule" : rule.type === "dispatchRuleIndividual" ? "Room per caller" : rule.type === "dispatchRuleDirect" ? "Shared room" : "Room per number",
        sub: p.rules.length > 1 ? `${rule.id} +${p.rules.length - 1}` : rule?.id,
        tags: rule ? [rule.pin === null ? "PIN unknown" : rule.pin ? "PIN" : "no PIN"] : undefined,
        issues: issuesFor(list, p.rules.map((r) => r.id)),
      },
      { icon: hopIcons.agent, title: rule?.agents ? "Agent" : rule?.agents === 0 ? "No agent" : "Agent unknown", sub: rule?.agents ? "name withheld" : undefined },
    ],
    links: [
      { label: "PSTN", secure: null, note: "The phone network leg, outside what LiveKit reports." },
      p.livekit
        ? { label: "LiveKit network", secure: null, note: "A LiveKit Phone Number: the provider leg is run by LiveKit and not reported." }
        : { label: media!, secure: media !== "RTP", note: `Media encryption on inbound trunk ${t!.id} is ${media === "RTP" ? "off, so audio arrives as plain RTP" : `${media}`}. Whether the provider signals over UDP, TCP or TLS is set on the provider side and is not in the API.` },
      { label: "in LiveKit", secure: null, note: "Bridged inside LiveKit. Not reported." },
      { label: "WebRTC", secure: true, note: "Inferred: WebRTC always encrypts with DTLS-SRTP. End-to-end encryption is off, so LiveKit can hear the audio." },
    ],
  }
}

export function outboundPath(t: Outbound, list: Check[]): Path {
  const media = enc(t.media.encryption)
  return {
    hops: [
      { icon: hopIcons.agent, title: "Agent", sub: "dials out" },
      { icon: hopIcons.room, title: "Room", sub: "CreateSIPParticipant" },
      { icon: hopIcons.sip, title: "LiveKit SIP", sub: t.id, tags: [t.auth === null ? "auth unknown" : t.auth ? "auth set" : "no auth"], issues: issuesFor(list, [t.id]) },
      { icon: hopIcons.provider, title: providerOf(t.address) ?? "Provider", sub: t.address, tags: [t.numbers.length === 1 ? "1 caller ID" : `${t.numbers.length} caller IDs`] },
      { icon: hopIcons.phone, title: "Callee", sub: "phone network" },
    ],
    links: [
      { label: "WebRTC", secure: true, note: "Inferred: WebRTC always encrypts with DTLS-SRTP." },
      { label: "in LiveKit", secure: null, note: "Bridged inside LiveKit. Not reported." },
      { label: `${transport(t.transport)} · ${media}`, secure: transport(t.transport) === "TLS" && media !== "RTP", note: `Signalling over ${transport(t.transport)}${transport(t.transport) === "TLS" ? "" : " (unencrypted)"}; media ${media === "RTP" ? "is plain RTP" : `is ${media}`}. From outbound trunk ${t.id}.` },
      { label: "PSTN", secure: null, note: "The phone network leg, outside what LiveKit reports." },
    ],
  }
}
