import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react"
import { BaseEdge, EdgeLabelRenderer, type EdgeProps, type Node as FlowNode, type Edge as FlowEdge, getBezierPath, type NodeProps } from "@xyflow/react"
import { Cloud, HardDrives, Phone, Robot, Users } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { Canvas } from "@/components/ai-elements/canvas"
import { Node } from "@/components/ai-elements/node"
import { ProviderLogo } from "@/components/provider-logo"
import { Tracker } from "@/components/tremor/Tracker"
import { AnimatedBeam } from "@/components/ui/animated-beam"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { fmtMs } from "@/features/call/call-model"

// "Live wire": the stack as a flow, callers to phone bridge to media server to each agent with calls
// to that agent's model providers. A link's thickness is its calls, its label the median ms measured
// on it ("not reported" where nothing measures it), and it turns red when that hop has issues. Beams
// run only on links that carried a call still open at the last read.

export type WireNode = { id: string; kind: "callers" | "phone" | "media" | "agent" | "provider"; title: string; sub: string; provider?: string; cloud?: boolean; issues?: number; tip?: string; y?: number }
export type WireLink = { id: string; from: string; to: string; calls: number | null; ms: number | null; msLabel?: string; issues: boolean; live: boolean; tip: string }
export type TrackerRow = { layer: string; blocks: { color: string; tooltip: string }[] }

const kindIcon: Record<WireNode["kind"], Icon> = { callers: Users, phone: Phone, media: HardDrives, agent: Robot, provider: Robot }
const column: Record<WireNode["kind"], number> = { callers: 0, phone: 250, media: 500, agent: 780, provider: 1110 }

// Node anchors for the beams: each node registers its left and right edge.
const Anchors = createContext<(id: string, side: "in" | "out", el: HTMLSpanElement | null) => void>(() => {})

function WireNodeView({ id, data }: NodeProps<FlowNode<WireNode>>) {
  const register = useContext(Anchors)
  const I = data.kind === "agent" && data.cloud ? Cloud : kindIcon[data.kind]
  return (
    <Node handles={{ target: data.kind !== "callers", source: data.kind !== "provider" }} className={cn("w-48 rounded-xl shadow-xs", data.issues ? "ring-1 ring-destructive/40" : "")}>
      <span ref={(el) => register(id, "in", el)} className="absolute top-1/2 left-0 size-px" />
      <span ref={(el) => register(id, "out", el)} className="absolute top-1/2 right-0 size-px" />
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="flex items-center gap-2.5 p-2.5">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border bg-background">{data.provider ? <ProviderLogo provider={data.provider} size={17} /> : <I size={16} />}</span>
            <span className="min-w-0">
              <span className="block truncate text-[12px] font-semibold">{data.title}</span>
              <span className="block truncate text-[11px] text-muted-foreground">{data.sub}</span>
            </span>
            {data.issues ? <span className="ml-auto rounded-full bg-destructive px-1.5 text-[10px] font-medium text-white tnum">{data.issues}</span> : null}
          </div>
        </TooltipTrigger>
        {data.tip && <TooltipContent className="max-w-72 text-xs leading-relaxed">{data.tip}</TooltipContent>}
      </Tooltip>
    </Node>
  )
}

function WireEdgeView({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data }: EdgeProps<FlowEdge<WireLink>>) {
  const [path, lx, ly] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition })
  const d = data!
  // An unknown count draws the thinnest line, never one sized as zero calls.
  const width = Math.min(1.5 + (d.calls ?? 0) * 0.7, 10)
  return (
    <>
      <BaseEdge id={id} path={path} style={{ strokeWidth: width, stroke: d.issues ? "var(--destructive)" : "var(--muted-foreground)", strokeOpacity: d.issues ? 0.55 : 0.28 }} />
      <EdgeLabelRenderer>
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className={cn(
                "nodrag nopan pointer-events-auto absolute z-10 rounded-md border bg-background px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap tnum shadow-xs",
                d.issues ? "border-destructive/40 text-destructive" : "text-muted-foreground",
                d.ms === null && !d.msLabel && "font-normal italic",
              )}
              style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}
            >
              {(d.msLabel ?? (d.ms === null ? "not reported" : fmtMs(d.ms))).split(" · ").map((line, i) => (
                <span key={i} className="block leading-tight">
                  {line}
                </span>
              ))}
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-72 text-xs leading-relaxed">{d.tip}</TooltipContent>
        </Tooltip>
      </EdgeLabelRenderer>
    </>
  )
}

const nodeTypes = { wire: WireNodeView }
const edgeTypes = { wire: WireEdgeView }

export function LiveWire({ nodes, links, live, trackers }: { nodes: WireNode[]; links: WireLink[]; live: string; trackers: TrackerRow[] }) {
  const container = useRef<HTMLDivElement>(null)
  const anchors = useRef(new Map<string, HTMLSpanElement>())
  const [ready, setReady] = useState(0)
  const register = useMemo(
    () => (id: string, side: "in" | "out", el: HTMLSpanElement | null) => {
      if (el) anchors.current.set(`${id}:${side}`, el)
    },
    [],
  )

  // Columns left to right, each centred on the same middle line.
  const flowNodes = useMemo<FlowNode<WireNode>[]>(() => {
    const byKind = new Map<string, WireNode[]>()
    for (const n of nodes) byKind.set(n.kind, [...(byKind.get(n.kind) ?? []), n])
    return nodes.map((n) => {
      const col = byKind.get(n.kind)!
      const gap = n.kind === "provider" ? 78 : 110
      const y = n.y ?? (col.indexOf(n) - (col.length - 1) / 2) * gap
      return { id: n.id, type: "wire", position: { x: column[n.kind], y }, data: n, draggable: false, selectable: false }
    })
  }, [nodes])
  const flowEdges = useMemo<FlowEdge<WireLink>[]>(() => links.map((l) => ({ id: l.id, source: l.from, target: l.to, type: "wire", data: l, selectable: false })), [links])

  // Beams need the nodes in their final place, after the canvas fits the view.
  useEffect(() => {
    const t = setTimeout(() => setReady((r) => r + 1), 500)
    return () => clearTimeout(t)
  }, [nodes])
  const beams = links.filter((l) => l.live)
  const ref = (key: string) => ({ current: anchors.current.get(key) ?? null })

  return (
    <div className="space-y-4">
      <div ref={container} className="relative h-[420px] overflow-hidden rounded-xl border">
        <Anchors.Provider value={register}>
          <Canvas
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={{ padding: 0.06, minZoom: 0.1 }}
            minZoom={0.1}
            panOnScroll={false}
            zoomOnScroll={false}
            zoomOnPinch={false}
            preventScrolling={false}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            selectionOnDrag={false}
            deleteKeyCode={null}
          />
        </Anchors.Provider>
        {ready > 0 &&
          beams.map((l, k) => (
            <AnimatedBeam
              key={`${l.id}-${ready}`}
              containerRef={container}
              fromRef={ref(`${l.from}:out`)}
              toRef={ref(`${l.to}:in`)}
              pathOpacity={0}
              pathWidth={Math.min(2 + (l.calls ?? 0) * 0.6, 6)}
              gradientStartColor="var(--primary)"
              gradientStopColor="var(--primary)"
              duration={2.6}
              delay={k * 0.3}
            />
          ))}
        <span className="pointer-events-none absolute top-3 left-3 z-10 flex items-center gap-1.5 rounded-full border bg-background/90 px-2 py-0.5 text-[11px] text-muted-foreground">
          <span className={cn("size-1.5 rounded-full", beams.length ? "animate-pulse bg-primary" : "bg-muted-foreground/40")} />
          {live}
        </span>
      </div>
      <div className="space-y-1.5">
        {trackers.map((t) => (
          <div key={t.layer} className="grid grid-cols-[112px_minmax(0,1fr)] items-center gap-3">
            <span className="truncate text-[12px] text-muted-foreground">{t.layer}</span>
            <Tracker data={t.blocks} className="h-5" hoverEffect />
          </div>
        ))}
        <div className="grid grid-cols-[112px_minmax(0,1fr)] gap-3 text-[10px] text-muted-foreground">
          <span />
          <span className="flex justify-between">
            <span>24 h before the last read</span>
            <span>last read</span>
          </span>
        </div>
      </div>
    </div>
  )
}
