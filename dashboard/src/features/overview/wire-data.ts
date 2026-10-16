import type { Call } from "@/lib/api"
import { providerOfModel } from "@/components/provider-logo"
import probe from "@/mocks/network-probe.json"
import sipRaw from "@/mocks/sip.json"
import { buildModel, fmtMs, median } from "@/features/call/call-model"
import { findingsFor } from "@/features/call/call-steps"
import type { Agent } from "@/features/agents/agents-model"
import { data as rooms, openAtWatchEnd } from "@/features/media-server/room-drawer"
import { type SipData, enc } from "@/features/sip/sip-model"
import { waterfallRows } from "@/features/profile/ttfa-waterfall"
import type { TrackerRow, WireLink, WireNode } from "./live-wire"

// Builds the Live wire from the captured data: who called, which hops each call crossed, which agent
// took it and which providers that agent used, with the median measured on each link.

type Row = { key: string; name: string; agent: Agent | null; callList: Call[] }
const providerName: Record<string, string> = { openai: "OpenAI", deepgram: "Deepgram", cartesia: "Cartesia", elevenlabs: "ElevenLabs", google: "Google", anthropic: "Anthropic" }
const roleName = { stt: "transcript", llm: "first token", tts: "first audio", rt: "realtime" } as const

export function wireData(rows: Row[], budget: number) {
  const sip = sipRaw as unknown as SipData
  const plain = [...sip.inbound, ...sip.outbound].filter((t) => enc(t.media.encryption) === "RTP").length
  const sipRooms = rooms.rooms.filter((r) => r.participants.some((p) => p.kind === "sip"))
  const phoneSids = new Set(sipRooms.map((r) => r.sid))
  const ring = sipRooms.flatMap((r) => r.participants.flatMap((p) => (p.sip?.inviteMs && p.sip.statuses.find((s) => s.status === "active") ? [(p.sip.statuses.find((s) => s.status === "active")!.t - p.sip.inviteMs / 1000) * 1000] : [])))
  const active = rows.filter((r) => r.callList.length)
  const all = active.flatMap((r) => r.callList)
  const phoneCalls = all.filter((c) => c.roomSid !== null && phoneSids.has(c.roomSid)).length
  const lastRead = Math.max(...rooms.windows.map((w) => w.end))

  // Rooms still open at the last read, and which agent was in them: the only calls drawn as live.
  const liveRooms = rooms.rooms.filter((r) => openAtWatchEnd(r) && r.participants.some((p) => p.kind === "agent" && openAtWatchEnd(p)))
  const liveAgents = new Set(liveRooms.flatMap((r) => r.participants.filter((p) => p.kind === "agent").map((p) => p.agentName ?? "unnamed")))
  const livePhone = liveRooms.some((r) => phoneSids.has(r.sid))

  const nodes: WireNode[] = [
    { id: "callers", kind: "callers", y: -20, title: "Callers", sub: `${all.length} calls`, tip: `${phoneCalls} by phone, ${all.length - phoneCalls} web, console or local test` },
    {
      id: "phone",
      kind: "phone",
      y: -150,
      title: "Phone bridge",
      sub: `${phoneCalls} ${phoneCalls === 1 ? "call" : "calls"}`,
      issues: plain || undefined,
      tip: plain ? `${plain} SIP trunks carry audio unencrypted (plain RTP)` : "Every SIP leg encrypted",
    },
    { id: "media", kind: "media", y: -20, title: "Media server", sub: `${rooms.rooms.length} rooms watched`, tip: "LiveKit Cloud" },
  ]
  const webIn = all.length - phoneCalls - (rows.find((r) => r.agent === null)?.callList.length ?? 0)
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
      calls: Math.max(webIn, 0),
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
    const turns = r.callList.flatMap((c) => buildModel(c).turns)
    const answers = turns.map((t) => t.responseMs).filter((v): v is number => v !== null)
    const issues = r.callList.reduce((n, c) => n + findingsFor(c, buildModel(c)).filter((f) => f.severity === "high" || f.severity === "medium").length, 0) + (r.agent ? r.agent.jobs.filter((j) => j.failed).length : 0)
    const isLive = liveAgents.has(r.agent?.name ?? "")
    nodes.push({
      id,
      kind: "agent",
      y: ay,
      title: r.name,
      sub: r.agent ? (r.agent.cloud ? "LiveKit Cloud" : "self-hosted") : "no agent named",
      cloud: Boolean(r.agent?.cloud),
      issues: issues || undefined,
      tip: `${r.callList.length} calls, ${answers.length} turns answered`,
    })
    const p50 = median(answers)
    // Calls whose agent is not named never showed up in a watched room, so their path is unknown.
    links.push(
      r.agent
        ? { id: `media-${id}`, from: "media", to: id, calls: r.callList.length, ms: p50, issues: p50 !== null && p50 > budget, live: isLive, tip: `Median answer, caller stops to agent speaks, over ${answers.length} turns (e2e_latency).` }
        : {
            id: `callers-${id}`,
            from: "callers",
            to: id,
            calls: r.callList.length,
            ms: p50,
            issues: p50 !== null && p50 > budget,
            live: false,
            tip: `Median answer over ${answers.length} turns. These calls ran outside the watched rooms (console and local tests), so their path is not reported.`,
          },
    )

    // Providers this agent used, from its own spans, with the stage each one served.
    const by = new Map<string, { calls: Set<string>; stt: number[]; llm: number[]; tts: number[]; rt: number[] }>()
    const add = (prov: string | null, role: "stt" | "llm" | "tts" | "rt", room: string, ms: number) => {
      if (!prov) return
      const v = by.get(prov) ?? { calls: new Set<string>(), stt: [], llm: [], tts: [], rt: [] }
      v.calls.add(room)
      v[role].push(ms)
      by.set(prov, v)
    }
    for (const c of r.callList) {
      const m = buildModel(c)
      for (const t of m.turns) {
        if (m.pipeline === "realtime") {
          if (t.responseMs !== null) add(providerOfModel(m.models.find((x) => x.includes("realtime")) ?? m.models[0]), "rt", c.room, t.responseMs)
          continue
        }
        for (const w of waterfallRows(c, t) ?? []) {
          const model = w.sub.replace(/ (first token|first audio)$/, "")
          if (w.label === "Speech to text") add(providerOfModel(model), "stt", c.room, w.end - w.start)
          else if (w.label === "LLM" || w.label === "LLM, call 1") add(providerOfModel(model), "llm", c.room, w.end - w.start)
          else if (w.label === "Text to speech") add(providerOfModel(model), "tts", c.room, w.end - w.start)
        }
      }
    }
    const provs = [...by.keys()]
    for (const [prov, v] of by) {
      const pid = `${id}:${prov}`
      const roles = (["stt", "llm", "tts", "rt"] as const).filter((k) => v[k].length)
      const far = probe.endpoints.find((e) => providerOfModel(e.model ?? "") === prov || e.provider.toLowerCase() === prov)
      const slow = probe.endpoints.filter((e) => e.provider.toLowerCase() === prov && e.tcpMs > 50)
      nodes.push({
        id: pid,
        kind: "provider",
        y: ay + (provs.indexOf(prov) - (provs.length - 1) / 2) * 78,
        title: providerName[prov] ?? prov,
        sub: roles.map((k) => ({ stt: "speech to text", llm: "LLM", tts: "voice", rt: "realtime" })[k]).join(", "),
        provider: prov,
        tip: far ? `${far.place}, ${fmtMs(far.tcpMs)} round trip from the agent host` : undefined,
      })
      links.push({
        id: `${id}-${pid}`,
        from: id,
        to: pid,
        calls: v.calls.size,
        ms: median(v[roles[0]]),
        msLabel: roles.map((k) => `${roleName[k]} ${fmtMs(median(v[k])!)}`).join(" · "),
        issues: slow.length > 0,
        live: isLive,
        tip: `${roles.map((k) => `${roleName[k]}: median ${fmtMs(median(v[k])!)} over ${v[k].length} turns`).join("; ")}.${slow.length ? ` ${slow.map((e) => `${e.host} is ${fmtMs(e.tcpMs)} away`).join(", ")} (over 50 ms).` : ""}`,
      })
    }
  }

  // 24 hourly blocks ending at the last read, one strip per layer.
  const hours = Array.from({ length: 24 }, (_, i) => lastRead - (24 - i) * 3600)
  const label = (h: number) => new Date(h * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
  const inHour = (h: number, t: number) => t >= h && t < h + 3600
  const grey = "bg-muted-foreground/15"
  const callsLayer = hours.map((h) => {
    const cs = all.filter((c) => inHour(h, Date.parse(c.startedAt) / 1000))
    const p = median(
      cs.flatMap((c) =>
        buildModel(c)
          .turns.map((t) => t.responseMs)
          .filter((v): v is number => v !== null),
      ),
    )
    if (!cs.length) return { color: grey, tooltip: `${label(h)} · no calls` }
    return {
      color: p === null ? "bg-muted-foreground/40" : p > budget ? "bg-destructive" : p > budget * 0.67 ? "bg-amber-500" : "bg-good",
      tooltip: `${label(h)} · ${cs.length} ${cs.length === 1 ? "call" : "calls"} · median answer ${p === null ? "not measured" : fmtMs(p)}`,
    }
  })
  const phoneLayer = hours.map((h) => {
    const rs = sipRooms.filter((r) => inHour(h, r.createdMs / 1000))
    return rs.length
      ? { color: plain ? "bg-amber-500" : "bg-good", tooltip: `${label(h)} · ${rs.length} phone ${rs.length === 1 ? "call" : "calls"}${plain ? " · legs unencrypted" : ""}` }
      : { color: grey, tooltip: `${label(h)} · no phone calls` }
  })
  const mediaLayer = hours.map((h) => {
    const watched = rooms.windows.some((w) => w.start < h + 3600 && w.end >= h)
    const rs = rooms.rooms.filter((r) => r.lastSeen >= h && r.firstSeen < h + 3600)
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
