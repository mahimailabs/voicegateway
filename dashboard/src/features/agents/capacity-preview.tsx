import { CapacityTopology } from "./capacity-topology"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { CapacitySessionFlow } from "./capacity-session-flow"
import { useEffect, useState } from "react"
import { WheelPicker, WheelPickerWrapper } from "@ncdai/react-wheel-picker"
import "@ncdai/react-wheel-picker/style.css"
import { Input } from "@/components/ui/input"
import { CheckCircle, Pause, Play, WarningCircle } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useUrlState } from "@/lib/url-state"

// Deliberately invented teaching scenarios. These never become CapacityRun records or API requests.
const levels = [1, 2, 4, 8, 16] as const
const concurrencyOptions = Array.from({ length: 64 }, (_, i) => ({ value: i + 1, label: String(i + 1) }))
export function CapacityPreview({ name, onClose }: { name: string; onClose: () => void }) {
  const [stepParam, setStep] = useUrlState("capacityStep", 8)
  const [view, setView] = useUrlState("capacityView", "capacity")
  const [mode, setMode] = useState("optimal")
  const [fixed, setFixed] = useState(16)
  const automaticStep = levels.includes(Number(stepParam) as typeof levels[number]) ? Number(stepParam) : 8
  const step = mode === "fixed" ? fixed : automaticStep
  const [scenario, setScenario] = useUrlState("capacityExample", "agent")
  const [callParam, setCall] = useUrlState("capacityCall", 1)
  const selectedCall = Math.min(step, Math.max(1, Math.floor(Number(callParam)) || 1))
  const [phase, setPhase] = useState<"setup" | "running" | "results">("setup")
  const [playing, setPlaying] = useState(false)
  const [animate, setAnimate] = useState(true)
  const atLimit = step > (scenario === "runner" ? 8 : 10)
  const runnerLimit = scenario === "runner"
  const waiting = atLimit && !runnerLimit ? step - 10 : 0
  const connected = atLimit && runnerLimit ? 8 : step - waiting
  useEffect(() => {
    if (!playing || phase !== "running") return
    const timer = setTimeout(() => {
      if (mode === "fixed" || atLimit || step === 16) {
        setPlaying(false)
        setPhase("results")
      } else setStep(levels[levels.indexOf(step as typeof levels[number]) + 1])
    }, 1800)
    return () => clearTimeout(timer)
  }, [playing, phase, step, setStep, mode, atLimit])
  const start = () => { setStep(1); setCall(1); setView("capacity"); setAnimate(true); setPhase("running"); setPlaying(true) }
  const configure = () => { setPlaying(false); setPhase("setup"); setView("capacity") }


  return <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
    <DialogContent className="grid-cols-1 [&>*]:min-w-0 max-h-[94dvh] overflow-y-auto sm:max-w-[1080px] p-5 sm:p-7 gap-6">
      <Tabs value={phase !== "setup" && view === "topology" ? "topology" : "capacity"} onValueChange={setView} className="gap-6 min-w-0">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between sm:pr-6">
          <DialogHeader className="pr-6 text-left">
            <div className="mb-2"><Badge variant="secondary">Preview · synthetic data</Badge></div>
            <DialogTitle className="text-2xl tracking-tight">Capacity test</DialogTitle>
            <DialogDescription>{name} · No live calls</DialogDescription>
          </DialogHeader>
          {phase !== "setup" && <TabsList aria-label="Capacity view" className="shrink-0"><TabsTrigger value="capacity">Capacity test</TabsTrigger><TabsTrigger value="topology">Topology</TabsTrigger></TabsList>}
        </div>
        <TabsContent value="capacity" className="space-y-6 min-w-0">
      <ol aria-label="Test progress" className="flex items-center gap-3 text-xs text-muted-foreground">
        {(["setup", "running", "results"] as const).map((item, i) => <li key={item} aria-current={phase === item ? "step" : undefined} className={phase === item ? "flex items-center gap-2 font-medium text-foreground" : "flex items-center gap-2"}><span className={phase === item ? "flex size-5 items-center justify-center rounded-full bg-primary text-primary-foreground" : "flex size-5 items-center justify-center rounded-full bg-muted"}>{i + 1}</span>{["Configure", "Run", "Results"][i]}{i < 2 && <span className="ml-2 text-muted-foreground">→</span>}</li>)}
      </ol>
      {phase === "setup" ? <section className="space-y-5" aria-label="Choose test traffic">
        <ToggleGroup type="single" value={mode} onValueChange={(value) => { if (value) { setPlaying(false); setMode(value) } }} className="flex-wrap justify-start" aria-label="Concurrency test mode">
          <ToggleGroupItem value="optimal">Find optimal concurrency</ToggleGroupItem>
          <ToggleGroupItem value="fixed">Fixed concurrency</ToggleGroupItem>
        </ToggleGroup>
        {mode === "optimal" ? <div className="flex flex-wrap items-center justify-between gap-4 py-3">
          <p className="text-sm text-muted-foreground">1 → 2 → 4 → 8 → 16 · Stop at threshold</p>

        </div> : <div className="flex items-center justify-between gap-6 rounded-2xl bg-muted/25 px-4 sm:px-6">
          <div className="min-w-0 space-y-2"><label htmlFor="fixed-concurrency" className="block text-sm font-medium">Concurrent sessions</label><Input id="fixed-concurrency" type="number" min={1} max={64} value={fixed} onChange={(event) => { const n = Number(event.target.value); if (Number.isFinite(n)) setFixed(Math.min(64, Math.max(1, Math.floor(n)))) }} className="w-24 bg-background tabular-nums" /><p className="text-xs text-muted-foreground">1–64 sessions</p></div>
          <div className="w-28 shrink-0" aria-label="Scroll to select concurrency"><WheelPickerWrapper><WheelPicker options={concurrencyOptions} value={fixed} onValueChange={setFixed} visibleCount={12} optionItemHeight={30} classNames={{ optionItem: "text-muted-foreground text-xl tabular-nums", highlightWrapper: "bg-accent rounded-xl", highlightItem: "text-foreground text-xl font-semibold tabular-nums" }} /></WheelPickerWrapper></div>
        </div>}

        <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4"><p className="text-xs text-muted-foreground">Synthetic preview only. No calls or provider charges.</p><Button onClick={start}><Play />Start preview</Button></div>
      </section> : <>
      <div className="flex flex-wrap items-center justify-between gap-3"><p role="status" className="text-sm">{phase === "results" ? "Preview complete" : playing ? `Running · ${step} concurrent sessions` : `Paused · ${step} concurrent sessions`}</p><div className="flex gap-2">{phase === "running" && <Button variant="outline" size="sm" onClick={() => setPlaying(!playing)}>{playing ? <Pause /> : <Play />}{playing ? "Pause" : "Resume"}</Button>}<Button variant="outline" size="sm" onClick={configure}>{phase === "running" ? "Cancel preview" : "New preview"}</Button></div></div>
      <section className="space-y-3" aria-label="Test traffic path">
        <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold">Session flow</h3><Button variant="ghost" size="sm" onClick={() => setAnimate(!animate)}>{animate ? <Pause /> : <Play />}{animate ? "Pause animation" : "Animate sessions"}</Button></div>
        <CapacitySessionFlow concurrency={step} connected={connected} runnerLimited={runnerLimit && atLimit} name={name} selected={selectedCall} onSelect={setCall} animate={animate && (phase === "results" || playing)} />
      </section>
      {phase === "results" && <Card className="shadow-none bg-muted/20">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">{atLimit ? <WarningCircle size={20} /> : <CheckCircle size={20} />}{!atLimit ? `${step} concurrent sessions passed (preview)` : runnerLimit ? "Load generator saturated" : (mode === "optimal" ? "Last passing concurrency: 8. Dispatch timeout at 16." : `Dispatch timeout at ${step} concurrent sessions (preview).`)}</CardTitle>
          <CardDescription>{!atLimit ? "All sessions within thresholds." : runnerLimit ? "Agent capacity undetermined. Runner saturation." : `${waiting} of ${step} sessions exceeded the 10 s dispatch timeout.`}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-3 gap-3 text-xs"><div className="space-y-1"><p className="text-muted-foreground">Connected</p><p className="text-lg font-semibold tnum">{connected} / {step}</p></div><div className="space-y-1"><p className="text-muted-foreground">p50 response latency</p><p className="text-lg font-semibold tnum">{runnerLimit && atLimit ? "Unknown" : `${(1.2 + Math.min(step, 16) / 40).toFixed(1)} s`}</p></div><div className="space-y-1"><p className="text-muted-foreground">Limiting signal</p><p className="text-lg font-semibold">{atLimit ? runnerLimit ? "Test runner" : "Agent dispatch" : "None shown"}</p></div></div>
        </CardContent>
      </Card>}
      </>}
        </TabsContent><TabsContent value="topology" className="min-w-0"><CapacityTopology connected={connected} requested={step} /></TabsContent>
      </Tabs>
      <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">About this preview</summary><p className="mt-2 max-w-prose leading-relaxed">Invented data and worker allocation. No provider requests or saved results. Peak component concurrency counts overlapping requests, not maximum capacity. Automatic mode reports the last passing test level; fixed mode tests one level. Provider regions require deployment or provider metadata. Realtime agents use a combined Realtime stage.</p></details>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4"><div className="space-y-2"><p className="text-xs text-muted-foreground">Try a different example</p><ToggleGroup type="single" value={scenario} onValueChange={(v) => { if (v) { configure(); setScenario(v) } }} className="flex-wrap" aria-label="Example bottleneck"><ToggleGroupItem value="agent">{"Agent dispatch timeout"}</ToggleGroupItem><ToggleGroupItem value="runner">Load generator saturation</ToggleGroupItem></ToggleGroup></div><Button variant="outline" onClick={onClose}>Close preview</Button></div>
    </DialogContent>
  </Dialog>
}
