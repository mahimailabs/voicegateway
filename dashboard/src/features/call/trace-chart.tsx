import { Bar, BarChart, Cell, LabelList, XAxis, YAxis } from "recharts"
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import type { Span } from "@/lib/api"
import { placed } from "./call-model"
import type { Focus } from "./call-view"

// The agent's OTLP span tree on one clock, indented by depth. Model spans are blue.
const llmSpans = new Set(["llm_node", "llm_request", "llm_request_run"])
const fmtMs = (v: number) => (v < 0.1 ? "<0.1 ms" : `${v < 10 ? v.toFixed(1) : Math.round(v)} ms`)

// Depth in the span tree, and which agent turn (by order) each span belongs to. Spans without a start or
// duration have no bar; they still count for depth and turn order.
function layout(spans: Span[]) {
  const byId = new Map(spans.map((s) => [s.spanId, s]))
  const turnIds = spans.filter((s) => s.name === "agent_turn").map((s) => s.spanId)
  return spans.filter(placed).map((s, i) => {
    let depth = 0
    let turn: number | null = null
    for (let cur: Span | undefined = s; cur; cur = cur.parentId ? byId.get(cur.parentId) : undefined) {
      if (cur.name === "agent_turn" && turn === null) turn = turnIds.indexOf(cur.spanId)
      if (cur !== s) depth += 1
    }
    return { i, name: s.name, depth, turn, durationMs: s.durationMs, llm: llmSpans.has(s.name), range: [s.startMs, s.startMs + Math.max(s.durationMs, 1)] as [number, number] }
  })
}


function SpanTick({ x, y, payload, rows }: { x?: number; y?: number; payload?: { value: number }; rows: { name: string; depth: number }[] }) {
  const r = rows[payload?.value ?? 0]
  if (!r) return null
  return (
    <text x={(x ?? 0) - 176 + r.depth * 12} y={y} dy={4} fontSize={12} fill="var(--foreground)">
      {r.name}
    </text>
  )
}

export function TraceChart({ spans, focus }: { spans: Span[]; focus: Focus }) {
  const rows = layout(spans)
  const maxMs = Math.ceil(Math.max(0, ...rows.map((r) => r.range[1])))
  return (
    <ChartContainer config={{ range: { label: "Span", color: "var(--chart-1)" } }} className="w-full" style={{ height: rows.length * 24 + 28 }}>
      <BarChart data={rows} layout="vertical" margin={{ left: 0, right: 72 }} barCategoryGap={5}>
        <XAxis type="number" domain={[0, maxMs]} tickLine={false} axisLine={false} tickFormatter={(v) => `${v} ms`} />
        <YAxis type="category" dataKey="i" width={184} tickLine={false} axisLine={false} interval={0} tick={<SpanTick rows={rows} />} />
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideIndicator labelFormatter={(_, p) => p?.[0]?.payload?.name} formatter={(_, __, item) => fmtMs(item.payload.durationMs)} />} />
        <Bar dataKey="range" radius={3} minPointSize={2} isAnimationActive={false}>
          {rows.map((r) => (
            <Cell key={r.i} fill={r.llm ? "var(--chart-1)" : "var(--muted-foreground)"} fillOpacity={focus === null || r.turn === focus.turn ? (r.llm ? 1 : 0.35) : 0.1} />
          ))}
          <LabelList dataKey="durationMs" position="right" fontSize={11} fill="var(--muted-foreground)" formatter={(v) => (typeof v === "number" ? fmtMs(v) : "")} />
        </Bar>
      </BarChart>
    </ChartContainer>
  )
}
