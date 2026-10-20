// Adapted from Aceternity UI World Map (MIT), https://ui.aceternity.com/components/world-map.
// Uses dotted-map's own projection for aligned markers; adds typed links and accessible hover details.
import { useMemo } from "react"
import { Cloud, Cpu, Robot, Pulse } from "@phosphor-icons/react"
import { motion, useReducedMotion } from "motion/react"
import DottedMap from "dotted-map"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
export type MapNode = { id: string; label: string; color: string; icon?: "sfu"; lat: number; lng: number; detail: string }
export type MapLink = { from: string; to: string; kind: "media" | "telemetry" }
export default function WorldMap({ nodes, links, animate = true }: { nodes: MapNode[]; links: MapLink[]; animate?: boolean }) {
  const reduced = useReducedMotion()
  const { svg, points, width, height } = useMemo(() => {
    const map = new DottedMap({ height: 80, grid: "diagonal" })
    const svg = map.getSVG({ radius: 0.24, color: "#b0b0b0", backgroundColor: "transparent" })
    const points = nodes.map((node) => ({ ...node, ...map.addPin({ lat: node.lat, lng: node.lng }) }))
    return { svg, points, width: map.image.width, height: map.image.height }
  }, [nodes])
  return <div className="relative w-full overflow-hidden rounded-xl bg-muted/20" style={{ aspectRatio: `${width} / ${height}` }}>
    <img src={`data:image/svg+xml;utf8,${encodeURIComponent(svg)}`} alt="World map with illustrative deployment regions" className="absolute inset-0 h-full w-full opacity-60 dark:opacity-40" />
    <svg viewBox={`0 0 ${width} ${height}`} className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">
      {links.map((link) => {
        const a = points.find((p) => p.id === link.from), b = points.find((p) => p.id === link.to)
        if (!a || !b) return null
        const d = `M ${a.x} ${a.y} Q ${(a.x + b.x) / 2} ${Math.min(a.y, b.y) - (link.kind === "media" ? 10 : -12)} ${b.x} ${b.y}`
        const media = link.kind === "media"
        return <g key={`${link.from}-${link.to}`}><path d={d} fill="none" stroke={media ? "var(--primary)" : "var(--muted-foreground)"} strokeWidth={0.3} strokeDasharray={media ? undefined : "1 1"} opacity={0.65} />
          {animate && !reduced && <motion.path d={d} fill="none" stroke={media ? "var(--primary)" : "var(--foreground)"} strokeWidth={0.65} strokeDasharray="1 30" animate={{ strokeDashoffset: [31, 0] }} transition={{ duration: media ? 2 : 4, repeat: Infinity, ease: "linear" }} />}</g>
      })}
    </svg>
    {points.map((point) => {
      const Icon = point.id === "runner" ? Cpu : (point.id === "sfu" || point.icon === "sfu") ? Cloud : point.id === "worker" ? Robot : Pulse
      const color = point.color
      return <Tooltip key={point.id} delayDuration={100}>
        <TooltipTrigger asChild>
          <button type="button" aria-label={point.label} style={{ left: `${point.x / width * 100}%`, top: `${point.y / height * 100}%` }} className="absolute flex size-5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full focus-visible:outline-2 focus-visible:outline-ring">
            <span style={{ backgroundColor: color }} className="size-2.5 rounded-full ring-2 ring-background transition-transform hover:scale-125" />
          </button>
        </TooltipTrigger>
        <TooltipContent sideOffset={6} className="max-w-64 gap-3 border bg-popover p-3 text-popover-foreground shadow-md [&>svg]:hidden">
          <Icon size={24} style={{ color }} className="shrink-0" />
          <div><strong className="block font-medium">{point.label}</strong><p className="mt-1 text-xs text-muted-foreground">{point.detail}</p></div>
        </TooltipContent>
      </Tooltip>
    })}
  </div>
}
