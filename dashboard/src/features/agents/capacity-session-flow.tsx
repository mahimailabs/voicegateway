import { CapacityWorkerDetails } from "./capacity-worker-details"
import { motion, useReducedMotion } from "motion/react"
import { ArrowRight, Cloud, Cpu, Robot, Waveform, WarningCircle } from "@phosphor-icons/react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { CapacityComponentMetrics } from "./capacity-component-metrics"

export function CapacitySessionFlow({ concurrency, connected, runnerLimited, name, selected, onSelect, animate }: { concurrency: number; connected: number; runnerLimited: boolean; name: string; selected: number; onSelect: (n: number) => void; animate: boolean }) {
  const reduced = useReducedMotion()
  const started = runnerLimited ? connected : concurrency
  const notGenerated = concurrency - started
  const pending = started - connected
  const sessions = (from: number, count: number, state: string) => Array.from({ length: count }, (_, i) => from + i).map((n) => <Button key={n} size="icon-sm" variant={selected === n ? "secondary" : "outline"} aria-label={`Session ${n}: ${state}`} aria-pressed={selected === n} onClick={() => onSelect(n)} className="size-8 rounded-full text-xs tabular-nums">{n}</Button>)
  const flow = <div className="relative h-px flex-1 bg-border" aria-hidden="true">{animate && !reduced && <motion.span className="absolute -top-1 size-2 rounded-full bg-primary" animate={{ left: ["0%", "96%"], opacity: [0, 1, 1, 0] }} transition={{ duration: 2, repeat: Infinity, ease: "linear" }} />}</div>
  return <section className="space-y-5" aria-label="Session routing map">
    <div className="grid gap-6 lg:grid-cols-[0.8fr_1.6fr]">
      <section className="space-y-5 rounded-2xl bg-muted/30 p-5" aria-label="Generated load">
        <div className="flex items-center justify-between"><h4 className="text-sm font-semibold">Generated load</h4><Cpu size={20} className="text-muted-foreground" /></div>
        <div><p className="text-4xl font-semibold tabular-nums">{started}<span className="ml-2 text-sm font-normal text-muted-foreground">/ {concurrency}</span></p><p className="mt-1 text-xs text-muted-foreground">Sessions started / requested</p></div>
        <Progress value={started / concurrency * 100} aria-label={`${started} of ${concurrency} requested sessions generated`} className="h-2" />
        <div className="flex items-center gap-3 text-xs"><Waveform size={20} /><span>Synthetic WebRTC audio</span>{flow}<ArrowRight size={18} /></div>
        <dl className="grid grid-cols-2 gap-3 border-t pt-4 text-xs"><div><dt className="text-muted-foreground">Runner CPU</dt><dd className="mt-1 text-lg font-semibold tabular-nums">{runnerLimited ? 96 : 24}%</dd></div><div><dt className="text-muted-foreground">Network egress</dt><dd className="mt-1 text-lg font-semibold tabular-nums">{(started * 0.06).toFixed(2)} <span className="text-xs font-normal">Mb/s</span></dd></div></dl>
        {notGenerated > 0 ? <div className="space-y-3 border-t pt-4"><p className="flex items-center gap-2 text-sm font-medium text-destructive"><WarningCircle />Runner saturated</p><p className="text-xs text-muted-foreground">{notGenerated} sessions not generated</p><div className="flex flex-wrap gap-2">{sessions(started + 1, notGenerated, "not generated")}</div></div> : <Badge variant="outline">All requested sessions generated</Badge>}
        <p className="text-[11px] text-muted-foreground">Isolated runner · illustrative telemetry</p>
      </section>
      <section className="min-w-0 space-y-4 rounded-2xl border p-5" aria-label="Observed target impact">
        <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-semibold">Target impact</h4><Badge variant="secondary">Preview</Badge></div>
        <div className="space-y-3 border-b pb-4">
          <div className="flex items-center gap-2"><Cloud size={20} /><h5 className="text-sm font-medium">LiveKit SFU</h5><span className="ml-auto text-xs text-muted-foreground">{started} rooms</span></div>
          <dl className="grid grid-cols-3 gap-2 text-xs"><div><dt className="text-muted-foreground">WebRTC RTT</dt><dd className="mt-1 text-lg font-semibold">42 <span className="text-xs font-normal">ms</span></dd></div><div><dt className="text-muted-foreground">Packet loss</dt><dd className="mt-1 text-lg font-semibold">0.2<span className="text-xs font-normal">%</span></dd></div><div><dt className="text-muted-foreground">SFU CPU</dt><dd className="mt-1 text-lg font-semibold">{Math.round(18 + started * 1.2)}<span className="text-xs font-normal">%</span></dd></div></dl>
          <p className="text-[11px] text-muted-foreground">RTT and loss: caller transport · CPU: SFU host</p>
        </div>
        <div className="space-y-3 border-b pb-4">
          <div className="flex items-center gap-2"><Robot size={20} /><h5 className="text-sm font-medium">Agent workers</h5><span className="ml-auto text-xs text-muted-foreground">{connected} joined</span></div>
          {[Math.ceil(connected / 2), Math.floor(connected / 2)].map((jobs, i) => <div key={i} className="grid grid-cols-[70px_1fr_auto] items-center gap-3 text-xs"><CapacityWorkerDetails index={i} jobs={jobs} /><Progress value={Math.min(100, jobs * 16)} aria-label={`Worker ${i + 1}: illustrative load ${jobs * 16}%`} className="h-2" /><span className="tabular-nums">{jobs} jobs · {jobs * 16}% load</span></div>)}
          <div className="flex flex-wrap justify-between gap-2 text-xs"><span className="text-muted-foreground truncate">{name}</span><span className={pending ? "text-destructive" : "text-muted-foreground"}>{pending ? `${pending} pending · dispatch > 10 s` : "No pending dispatch"}</span></div>
          {pending > 0 && <div className="flex flex-wrap gap-2">{sessions(connected + 1, pending, "pending dispatch")}</div>}
        </div>
        <div className="flex items-center gap-3"><Waveform size={20} /><div><h5 className="text-sm font-medium">Inference providers</h5><p className="text-xs text-muted-foreground">STT → LLM → TTS</p></div><span className="ml-auto text-xs text-muted-foreground">Metrics below ↓</span></div>
      </section>
    </div>
    <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">Inspect generated sessions ({started})</summary><div className="mt-3 flex flex-wrap gap-2">{sessions(1, started, "generated")}</div></details>
    <CapacityComponentMetrics concurrency={concurrency} connected={connected} />
  </section>
}
