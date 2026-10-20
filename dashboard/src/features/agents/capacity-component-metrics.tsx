import { CartesianGrid, Line, LineChart, ReferenceDot, ReferenceLine, XAxis, YAxis } from "recharts"
import { Badge } from "@/components/ui/badge"
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { ProviderLogo } from "@/components/provider-logo"

// Fictional fixtures for layout exploration, not benchmarks for these providers/models.
const examples = [
  { stage: "STT", provider: "deepgram", brand: "Deepgram", model: "nova-3", metric: "p95 transcription delay", base: 160, slope: 10, target: 400, overlap: 1, throughput: 0.96, unit: "audio s/s", throughputLabel: "Audio processed" },
  { stage: "LLM", provider: "openai", brand: "OpenAI", model: "gpt-4.1-mini", metric: "p95 TTFT", base: 260, slope: 24, target: 600, overlap: 0.75, throughput: 38, unit: "tokens/s", throughputLabel: "Output throughput" },
  { stage: "TTS", provider: "cartesia", brand: "Cartesia", model: "sonic-2", metric: "p95 TTFB", base: 70, slope: 8, target: 250, overlap: 0.5, throughput: 2.4, unit: "audio s/s", throughputLabel: "Audio generated" },
]

export function CapacityComponentMetrics({ concurrency, connected }: { concurrency: number; connected: number }) {
  return <section className="space-y-5 border-t pt-5" aria-label="Component metrics">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-semibold">Component metrics</h4><Badge variant="outline">Synthetic examples</Badge></div>
    <div className="grid gap-7 md:grid-cols-3">{examples.map((example) => {
      const peak = Math.ceil(connected * example.overlap)
      const latencyAt = (n: number) => Math.round(example.base + n * example.slope + Math.max(0, n - 8) ** 1.5 * 8)
      const levels = [...new Set([1, 2, 4, 8, 16, concurrency])].sort((a, b) => a - b)
      const data = levels.map((sessions) => ({ sessions, latency: latencyAt(sessions) }))
      const latency = latencyAt(concurrency)
      const throughput = (peak * example.throughput).toFixed(example.stage === "LLM" ? 0 : 1)
      return <article key={example.stage} className="min-w-0 space-y-4" aria-label={`${example.stage} preview metrics`}>
        <div className="flex items-center justify-between gap-2"><div className="space-y-1"><h5 className="text-sm font-semibold">{example.stage}</h5><p className="text-xs text-muted-foreground">{example.model}</p></div><div className="flex items-center gap-1.5 text-xs"><ProviderLogo provider={example.provider} size={18} />{example.brand}</div></div>
        <dl className="grid grid-cols-2 gap-3">
          <div><dt className="text-xs text-muted-foreground">Peak concurrency</dt><dd className="text-2xl font-semibold tabular-nums">{peak}<span className="ml-1 text-[11px] font-normal text-muted-foreground">{example.stage === "STT" ? "streams" : "requests"}</span></dd></div>
          <div><dt className="text-xs text-muted-foreground">{example.throughputLabel}</dt><dd className="text-2xl font-semibold tabular-nums">{throughput}<span className="ml-1 text-[11px] font-normal text-muted-foreground">{example.unit}</span></dd></div>
        </dl>
        <div className="space-y-2 rounded-xl bg-muted/25 p-3">
          <div className="flex items-center justify-between gap-2 text-xs"><span className="text-muted-foreground">{example.metric}</span><span className="font-medium tabular-nums">{latency} ms</span></div>
          <ChartContainer config={{ latency: { label: "p95 latency (ms)", color: "var(--primary)" } }} className="h-36 w-full aspect-auto" aria-label={`${example.stage}: illustrative latency versus session concurrency; selected ${concurrency} sessions, ${latency} milliseconds; example target ${example.target} milliseconds`}>
            <LineChart accessibilityLayer data={data} margin={{ top: 12, right: 8, bottom: 0, left: -20 }}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="sessions" type="number" domain={[1, Math.max(16, concurrency)]} ticks={levels} tickLine={false} axisLine={false} minTickGap={14} />
              <YAxis tickLine={false} axisLine={false} domain={[0, Math.ceil(Math.max(example.target * 1.3, latencyAt(Math.max(16, concurrency)) * 1.1) / 100) * 100]} tickCount={3} tickFormatter={(value: number) => Math.round(value).toLocaleString()} />
              <ChartTooltip content={<ChartTooltipContent labelFormatter={(value) => `${value} sessions`} />} />
              <ReferenceLine y={example.target} stroke="var(--muted-foreground)" strokeDasharray="4 4" />
              <Line type="monotone" dataKey="latency" stroke="var(--color-latency)" strokeWidth={2} dot={false} isAnimationActive={false} />
              <ReferenceDot x={concurrency} y={latency} r={4} fill="var(--primary)" stroke="var(--background)" strokeWidth={2} />
            </LineChart>
          </ChartContainer>
          <div className="flex justify-between gap-2 text-[10px] text-muted-foreground"><span>Concurrent sessions →</span><span>Dashed: {example.target} ms target</span></div>
        </div>
      </article>
    })}</div>
    <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">Example workload & regions</summary><p className="mt-2 leading-relaxed">Fictional provider/model selection, throughput and sweep curves, including points beyond the selected concurrency. Not measured benchmarks. Throughput is aggregate output or processed audio per wall-clock second. Regions: unknown. Targets are illustrative, not provider guarantees.</p></details>
  </section>
}
