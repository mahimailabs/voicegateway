import { useMemo } from "react"
import { Microphone, Brain, SpeakerHigh, Phone } from "@phosphor-icons/react"
import { type MapLink, type MapNode } from "@/components/ui/world-map"
import { Badge } from "@/components/ui/badge"
import { TopologyMap } from "@/components/ui/topology-map"

export function CapacityTopology({ connected, requested }: { connected: number; requested: number }) {
  const nodes = useMemo<MapNode[]>(() => [
    { id: "runner", color: "#2563eb", label: "Load generator", lat: 51.5, lng: -0.12, detail: `London · example runner · ${requested} requested sessions` },
    { id: "sfu", color: "#0d9488", label: "LiveKit SFU", lat: 50.11, lng: 8.68, detail: `Frankfurt · example region · ${connected} connected sessions` },
    { id: "worker", color: "#9333ea", label: "Agent workers", lat: 43.65, lng: -79.38, detail: `Toronto · 2 example workers · ${connected} active jobs · 2 vCPU / 4 GiB each` },
    { id: "vg", color: "#d97706", label: "VoiceGateway", lat: 38.9, lng: -77.04, detail: "Virginia · example telemetry collector · outside the audio path" },
  ], [connected, requested])
  const links: MapLink[] = [
    { from: "runner", to: "sfu", kind: "media" }, { from: "sfu", to: "worker", kind: "media" },
    { from: "runner", to: "vg", kind: "telemetry" }, { from: "sfu", to: "vg", kind: "telemetry" }, { from: "worker", to: "vg", kind: "telemetry" },
  ]
  return <section className="space-y-4" aria-label="Deployment topology preview">
    <div className="flex items-center justify-between gap-2"><Badge variant="secondary">Example deployment</Badge><span className="text-xs text-muted-foreground">Drag to pan · Scroll or pinch to zoom</span></div>
    <TopologyMap nodes={nodes} links={links} />
    <div className="flex flex-wrap gap-5 text-xs text-muted-foreground"><span className="flex items-center gap-2"><span className="w-7 border-t-2 border-primary" />Media ↔</span><span className="flex items-center gap-2"><span className="w-7 border-t border-dashed border-foreground" />Telemetry → VoiceGateway</span></div>
    <div className="flex flex-wrap items-center gap-2 border-t pt-3"><span className="mr-1 text-xs text-muted-foreground">Unlocated</span>{[{ label: "STT", Icon: Microphone }, { label: "LLM", Icon: Brain }, { label: "TTS", Icon: SpeakerHigh }, { label: "SIP · optional", Icon: Phone }].map(({ label, Icon }) => <Badge key={label} variant="outline"><Icon size={14} />{label}</Badge>)}</div>
    <p className="text-[11px] text-muted-foreground">Illustrative regions and logical routes. SIP is outside this WebRTC test.</p>
  </section>
}
