import type { CallStats, ProviderProbes } from "@/lib/api-types"
import { providerOfModel } from "@/components/provider-logo"
import { fmtMs, median } from "@/features/call/call-model"
import type { Agent } from "@/features/agents/agents-model"
import { type MediaServerData, openAtWatchEnd } from "@/features/media-server/room-drawer"
import { type SipData, enc } from "@/features/sip/sip-model"
import type { TrackerRow, WireLink, WireNode } from "./live-wire"

// Builds the Live wire from the reads: who called (getCallStats), which hops each call crossed (the
// media server watch and SIP), which agent took it and which providers that agent used, with the
// median measured on each link.

type Row = { key: string; name: string; agent: Agent | null; stats: CallStats["agents"][number] | null }
export type WireReads = { stats: CallStats; ms: MediaServerData; sip: SipData; probes: ProviderProbes }
const providerName: Record<string, string> = { openai: "OpenAI", deepgram: "Deepgram", cartesia: "Cartesia", elevenlabs: "ElevenLabs", google: "Google", anthropic: "Anthropic" }
const roleName = { stt: "transcript", llm: "first token", tts: "first audio", rt: "realtime" } as const

export function wireData(rows: Row[], { stats, ms: rooms, sip, probes: probe }: WireReads, budget: number) {
  const plain = [...sip.inbound, ...sip.outbound].filter((t) => enc(t.media.encryption) === "RTP").length
  const sipRooms = rooms.rooms.filter((r) => r.participants.some((p) => p.kind === "sip"))
  const phoneSids = new Set(sipRooms.map((r) => r.sid))
  const ring = sipRooms.flatMap((r) => r.participants.flatMap((p) => (p.sip?.inviteMs != null && p.sip.statuses.find((s) => s.status === "active")?.t != null ? [(p.sip.statuses.find((s) => s.status === "active")!.t! - p.sip.inviteMs / 1000) * 1000] : [])))
  const active = rows.filter((r) => r.stats && r.stats.callCount !== 0)
  const all = stats.callCount
  const phoneCalls = stats.byChannel.phone
  const n = (v: number | null, one: string, many: string) => (v === null ? `unknown ${many}` : `${v} ${v === 1 ? one : many}`)
  // The latest known end of a watch; NaN when none was reported, which places no room as live.
  const ends = rooms.windows.flatMap((w) => (w.end === null ? [] : [w.end]))
  const lastRead = ends.length ? Math.max(...ends) : NaN

  // Rooms still open at the last read, and which agent was in them: the only calls drawn as live.
  const liveRooms = rooms.rooms.filter((r) => openAtWatchEnd(r, rooms) && r.participants.some((p) => p.kind === "agent" && openAtWatchEnd(p, rooms)))
  const liveAgents = new Set(liveRooms.flatMap((r) => r.participants.filter((p) => p.kind === "agent").map((p) => p.agentName ?? "unnamed")))
  const livePhone = liveRooms.some((r) => phoneSids.has(r.sid))

  const nodes: WireNode[] = [
    { id: "callers", kind: "callers", y: -20, title: "Callers", sub: n(all, "call", "calls"), tip: all === null || phoneCalls === null ? "How many calls came by phone is unknown" : `${phoneCalls} by phone, ${all - phoneCalls} web, console or local test` },
    {
      id: "phone",
      kind: "phone",
      y: -150,
      title: "Phone bridge",
      sub: n(phoneCalls, "call", "calls"),
      issues: plain || undefined,
      tip: plain ? `${plain} SIP trunks carry audio unencrypted (plain RTP)` : "Every SIP leg encrypted",
    },
    { id: "media", kind: "media", y: -20, title: "Media server", sub: `${rooms.rooms.length} rooms watched`, tip: "LiveKit Cloud" },
  ]
  // Calls that reached an agent through the media server without a phone leg; unknown if any count is.
  const unnamed = rows.find((r) => r.key === "other")?.stats?.callCount ?? 0
  const webIn = all === null || phoneCalls === null || unnamed === null ? null : Math.max(all - phoneCalls - unnamed, 0)
  const links: WireLink[] = [
    {
      id: "callers-phone",
      from: "callers",
      to: "phone",
      calls: phoneCalls,
      ms: median(ring),
      msLabel: ring.length ? `setup ${fmtMs(median(ring)!)}` : undefined,
      issues: false,
      live: livePhone,
      tip: `Ring to answered, median of ${ring.length} watched phone calls (SIP participant statuses).`,
    },
    { id: "phone-media", from: "phone", to: "media", calls: phoneCalls, ms: null, issues: plain > 0, live: livePhone, tip: `Audio delay on the SIP leg is not reported by LiveKit.${plain ? ` ${plain} trunks carry it unencrypted.` : ""}` },
    {
      id: "callers-media",
      from: "callers",
      to: "media",
      calls: webIn,
      ms: null,
      issues: false,
      live: liveRooms.some((r) => !phoneSids.has(r.sid)),
      tip: "The caller's own network is only visible to their app (WebRTC stats); LiveKit does not report it to the agent.",
    },
  ]

  const agentY = (r: Row) => (r.agent ? -20 - active.filter((x) => x.agent).indexOf(r) * 160 : 190)
  for (const r of active) {
    const id = `agent:${r.key}`
    const ay = agentY(r)
    const st = r.stats!
    const failed = r.agent ? r.agent.jobs.filter((j) => j.failed).length : 0
    // With findings unknown the node shows only the failed jobs it does know, and its tip says so.
    const issues = st.issues === null ? failed : st.issues + failed
    const isLive = liveAgents.has(r.agent?.name ?? "")
    nodes.push({
      id,
      kind: "agent",
      y: ay,
      title: r.name,
      sub: r.agent ? (r.agent.cloud ? "LiveKit Cloud" : "self-hosted") : "no agent named",
      cloud: Boolean(r.agent?.cloud),
      issues: issues || undefined,
      tip: `${n(st.callCount, "call", "calls")}, ${n(st.answerTurns, "turn", "turns")} answered${st.issues === null ? ", findings unknown" : ""}`,
    })
    const p50 = st.answerP50Ms
    const answered = st.answerTurns === null ? "an unknown number of" : String(st.answerTurns)
    // Calls whose agent is not named never showed up in a watched room, so their path is unknown.
    links.push(
      r.agent
        ? { id: `media-${id}`, from: "media", to: id, calls: st.callCount, ms: p50, issues: p50 !== null && p50 > budget, live: isLive, tip: `Median answer, caller stops to agent speaks, over ${answered} turns (e2e_latency).` }
        : {
            id: `callers-${id}`,
            from: "callers",
            to: id,
            calls: st.callCount,
            ms: p50,
            issues: p50 !== null && p50 > budget,
            live: false,
            tip: `Median answer over ${answered} turns. These calls ran outside the watched rooms (console and local tests), so their path is not reported.`,
          },
    )

    // Providers this agent used, from the model named on each stage's span (getCallStats).
    const provs = st.providers.map((p) => p.provider)
    const roleKey = { stt: "stt", llm: "llm", tts: "tts", realtime: "rt" } as const
    for (const pu of st.providers) {
      const prov = pu.provider
      const pid = `${id}:${prov}`
      const roles = pu.roles
      const far = probe.endpoints.find((e) => providerOfModel(e.model ?? "") === prov || e.provider.toLowerCase() === prov)
      const slow = probe.endpoints.filter((e) => e.provider.toLowerCase() === prov && e.tcpMs !== null && e.tcpMs > 50)
      const ms = (v: number | null) => (v === null ? "unknown" : fmtMs(v))
      nodes.push({
        id: pid,
        kind: "provider",
        y: ay + (provs.indexOf(prov) - (provs.length - 1) / 2) * 78,
        title: providerName[prov] ?? prov,
        sub: roles.map((k) => ({ stt: "speech to text", llm: "LLM", tts: "voice", realtime: "realtime" })[k.role]).join(", "),
        provider: prov,
        tip: far ? `${far.place}, ${ms(far.tcpMs)} round trip from the agent host` : undefined,
      })
      links.push({
        id: `${id}-${pid}`,
        from: id,
        to: pid,
        calls: pu.callCount,
        ms: roles[0]?.medianMs ?? null,
        msLabel: roles.map((k) => `${roleName[roleKey[k.role]]} ${ms(k.medianMs)}`).join(" · "),
        issues: slow.length > 0,
        live: isLive,
        tip: `${roles.map((k) => `${roleName[roleKey[k.role]]}: median ${ms(k.medianMs)} over ${k.turns ?? "an unknown number of"} turns`).join("; ")}.${slow.length ? ` ${slow.map((e) => `${e.host} is ${ms(e.tcpMs)} away`).join(", ")} (over 50 ms).` : ""}`,
      })
    }
  }

  // 24 UTC-hour blocks, the last one holding the last read, one strip per layer.
  const lastHour = Math.floor(lastRead / 3600) * 3600
  const hours = Array.from({ length: 24 }, (_, i) => lastHour - (23 - i) * 3600)
  const label = (h: number) => new Date(h * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
  const inHour = (h: number, t: number) => t >= h && t < h + 3600
  const grey = "bg-muted-foreground/15"
  // The calls strip reads the server's hourly counts: each block is the UTC hour it falls in.
  const callsLayer = hours.map((h) => {
    const b = stats.byHour.find((x) => Date.parse(x.hour) / 1000 === h)
    if (!b) return { color: grey, tooltip: `${label(h)} · outside the range read` }
    if (b.callCount === null) return { color: "bg-muted-foreground/40", tooltip: `${label(h)} · calls unknown` }
    if (!b.callCount) return { color: grey, tooltip: `${label(h)} · no calls` }
    const p = b.answerP50Ms
    return {
      color: p === null ? "bg-muted-foreground/40" : p > budget ? "bg-destructive" : p > budget * 0.67 ? "bg-amber-500" : "bg-good",
      tooltip: `${label(h)} · ${b.callCount} ${b.callCount === 1 ? "call" : "calls"} · median answer ${p === null ? "not measured" : fmtMs(p)}`,
    }
  })
  const phoneLayer = hours.map((h) => {
    const rs = sipRooms.filter((r) => r.createdMs !== null && inHour(h, r.createdMs / 1000))
    return rs.length
      ? { color: plain ? "bg-amber-500" : "bg-good", tooltip: `${label(h)} · ${rs.length} phone ${rs.length === 1 ? "call" : "calls"}${plain ? " · legs unencrypted" : ""}` }
      : { color: grey, tooltip: `${label(h)} · no phone calls` }
  })
  const mediaLayer = hours.map((h) => {
    const watched = rooms.windows.some((w) => w.start !== null && w.end !== null && w.start < h + 3600 && w.end >= h)
    const rs = rooms.rooms.filter((r) => r.lastSeen !== null && r.firstSeen !== null && r.lastSeen >= h && r.firstSeen < h + 3600)
    return watched ? { color: "bg-good", tooltip: `${label(h)} · watched · ${rs.length} rooms seen` } : { color: grey, tooltip: `${label(h)} · not watched` }
  })
  const jobs = active.flatMap((r) => r.agent?.jobs ?? [])
  const agentLayer = hours.map((h) => {
    const js = jobs.filter((j) => Number.isFinite(j.dispatchedMs) && inHour(h, j.dispatchedMs / 1000))
    if (!js.length) return { color: grey, tooltip: `${label(h)} · no jobs` }
    const failed = js.filter((j) => j.failed).length
    return { color: failed ? "bg-destructive" : "bg-good", tooltip: `${label(h)} · ${js.length} jobs${failed ? ` · ${failed} failed` : ""}` }
  })
  const trackers: TrackerRow[] = [
    { layer: "Calls", blocks: callsLayer },
    { layer: "Phone bridge", blocks: phoneLayer },
    { layer: "Media server", blocks: mediaLayer },
    { layer: "Agents", blocks: agentLayer },
  ]

  const live = liveRooms.length ? `${liveRooms.length} ${liveRooms.length === 1 ? "call" : "calls"} open at the last read, ${label(lastRead)}` : `No calls open at the last read, ${label(lastRead)}`
  return { nodes, links, trackers, live }
}
