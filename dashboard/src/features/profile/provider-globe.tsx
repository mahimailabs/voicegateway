import { useMemo } from "react"
import { type GlobeArc, type GlobePoint, NetworkGlobe } from "@/components/network-globe"
import type { ProviderProbes } from "@/lib/api-types"

// Where the call's pieces are: the agent worker, the media server and each model provider, with the
// measured round trip from the agent drawn as an arc (red when far). A place the probe could not
// locate is left off the globe.

const roleName: Record<string, string> = { stt: "Speech to text", llm: "LLM", tts: "Text to speech", sfu: "Media server" }
type Located<T> = T & { lat: number; lng: number }
const located = <T extends { lat: number | null; lng: number | null }>(x: T): x is Located<T> => x.lat !== null && x.lng !== null

export function ProviderGlobe({ probes, size = 420 }: { probes: ProviderProbes; size?: number }) {
  const agent = probes.agent
  const points = useMemo<GlobePoint[]>(() => {
    const seen = new Map<string, GlobePoint & { roles: string[] }>()
    if (located(agent)) seen.set(`${agent.lat},${agent.lng}`, { lat: agent.lat, lng: agent.lng, label: agent.label, roles: [], tone: "ink" })
    for (const e of probes.endpoints.filter(located)) {
      const k = `${e.lat},${e.lng}`
      const p = seen.get(k) ?? { lat: e.lat, lng: e.lng, label: e.place, roles: [], tone: "accent" as const }
      p.roles.push(roleName[e.role])
      seen.set(k, p)
    }
    return [...seen.values()].map((p) => ({ ...p, detail: p.roles.join(", ") }))
  }, [agent, probes.endpoints])
  const arcs: GlobeArc[] = located(agent)
    ? probes.endpoints
        .filter(located)
        .filter((e, k, all) => all.findIndex((x) => x.host === e.host) === k)
        .map((e) => ({ from: agent, to: e, label: e.tcpMs === null ? `Round trip to ${e.host} not measured` : `${e.tcpMs} ms round trip to ${e.host}`, tone: e.tcpMs === null ? "quiet" : e.tcpMs > 50 ? "alert" : "accent" }))
    : []
  return <NetworkGlobe points={points} arcs={arcs} size={size} />
}
