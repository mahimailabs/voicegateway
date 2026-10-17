import { useMemo, useState } from "react"
import { Link, useNavigate } from "@tanstack/react-router"
import { Brain, CaretRight, Cloud, DownloadSimple, Gauge, HardDrives, Microphone, Phone, PhoneCall, Robot, SpeakerHigh, UploadSimple, Users, WarningCircle } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { ProviderLogo, providerOfModel } from "@/components/provider-logo"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { listCalls } from "@/lib/api"
import { BUDGET_MS } from "@/lib/thresholds"
import { cn } from "@/lib/utils"
import egressRaw from "@/mocks/egress.json"
import ingressRaw from "@/mocks/ingress.json"
import probe from "@/mocks/network-probe.json"
import sipRaw from "@/mocks/sip.json"
import workers from "@/mocks/workers.json"
import { buildModel, fmtMs, median } from "@/features/call/call-model"
import { findingsFor } from "@/features/call/call-steps"
import { type Agent, TEST_AGENTS, agents, joinDelays } from "@/features/agents/agents-model"
import { peakAtOnce } from "@/features/agents/agent-drawer"
import { issues } from "@/features/issues/issues-model"
import { data as rooms, regionCity } from "@/features/media-server/room-drawer"
import { type SipData, checks as sipChecks, enc, routes } from "@/features/sip/sip-model"
import { ProviderGlobe } from "@/features/profile/provider-globe"
import { LatencyChain } from "./latency-chain"
import { AnswerGauge, CallTracker, TurnAnatomy, TurnStrip, anatomy } from "./visuals"
import { LiveWire } from "./live-wire"
import { wireData } from "./wire-data"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useUrlState } from "@/lib/url-state"

// The whole stack at a glance: four numbers with their shape, the path a call takes with each part's
// state, where a turn's time goes, and where the models are. Every figure comes from the same data and
// checks as the page it links to; words are kept to labels.

export const fmtS = (ms: number) => `${(ms / 1000).toFixed(2)} s`
const day = (iso: string) => new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" })

export function useOverview() {
  return useMemo(() => {
    const calls = listCalls().filter((c) => !c.recording?.preview)
    const models = calls.map((c) => ({ c, m: buildModel(c) }))
    const turns = models.flatMap(({ m }) => m.turns)
    const answers = turns.map((t) => t.responseMs).filter((v): v is number => v !== null)
    const perCall = models
      .map(({ c, m }) => ({ room: c.room, at: Date.parse(c.startedAt), p50: median(m.turns.map((t) => t.responseMs).filter((v): v is number => v !== null)) }))
      .filter((x): x is { room: string; at: number; p50: number } => x.p50 !== null)
      .sort((a, b) => a.at - b.at)
    const sorted = [...calls].sort((a, b) => Date.parse(a.startedAt) - Date.parse(b.startedAt))
    const byDay = [...sorted.reduce((m, c) => m.set(day(c.startedAt), (m.get(day(c.startedAt)) ?? 0) + 1), new Map<string, number>())].map(([d, n]) => ({ d, n }))

    // Where a turn's time goes: medians over every turn that reported the stage. Each part is its own
    // median, so they need not add up to the median answer; the bar says so.
    const med = (f: (t: (typeof turns)[number]) => number | null) => median(turns.map(f).filter((v): v is number => v !== null))
    const parts = [
      { key: "eot", label: "Waiting for the turn to end", ms: med((t) => t.eotMs), color: "bg-muted-foreground/40" },
      { key: "llm", label: "Model first token", ms: med((t) => t.ttftMs), color: "bg-primary" },
      { key: "tts", label: "Voice first audio", ms: med((t) => t.ttsMs), color: "bg-primary/55" },
    ].filter((p): p is { key: string; label: string; ms: number; color: string } => p.ms !== null)

    const found = issues()
    const sev = (s: string) => found.filter((i) => i.severity === s).length

    const fleet = agents()
    const failed = fleet.flatMap((a) => a.jobs.filter((j) => j.failed).map(() => a.name ?? a.key))
    const ws = workers.workers as { samples: { t: string; activeJobs: number }[] }[]
    const n = Math.min(...ws.map((w) => w.samples.length))
    const load = Array.from({ length: n }, (_, i) => ({ i, calls: ws.reduce((a, w) => a + w.samples[i].activeJobs, 0) }))

    const sip = sipRaw as unknown as SipData
    const hints = rooms.rooms.flatMap((r) => r.participants.filter((p) => p.sip).map((p) => ({ trunkId: p.sip!.trunkId, twilio: Boolean(p.sip!.twilio) })))
    const rows = routes(sip, hints)
    const legs = [...sip.inbound.map((t) => enc(t.media.encryption)), ...sip.outbound.map((t) => enc(t.media.encryption))]
    const sipFound = sipChecks(sip, rows)

    const eg = (egressRaw as unknown as { egress: { status: string }[] }).egress
    const ing = (ingressRaw as unknown as { ingress: { status: string }[] }).ingress
    const regions = [...new Set(rooms.rooms.flatMap((r) => r.participants.map((p) => p.region)).filter((r): r is string => Boolean(r)))]

    // Which agent took each call: the agent that joined its room in the media server's watch. Console
    // and local test sessions name no agent, so they are their own row, not an agent's.
    const agentOf = new Map<string, string>()
    for (const r of rooms.rooms) {
      const a = r.participants.find((p) => p.kind === "agent")
      if (a) agentOf.set(r.sid, a.agentName ?? "unnamed")
    }
    // Otherwise the agent's own trace names it (lk.agent_name on its spans).
    const agentName = (c: (typeof calls)[number]) => (c.roomSid !== null ? agentOf.get(c.roomSid) : undefined) ?? (c.spans ?? []).map((sp) => sp.attrs?.["lk.agent_name"]).find((n) => n) ?? null
    const rowsFor = (key: string | null) => models.filter(({ c }) => agentName(c) === key)
    const stats = (list: typeof models) => {
      const ts = list.flatMap(({ m }) => m.turns)
      const ans = ts.map((t) => t.responseMs).filter((v): v is number => v !== null)
      const md = (f: (t: (typeof ts)[number]) => number | null) => median(ts.map(f).filter((v): v is number => v !== null))
      return {
        callList: list.map(({ c }) => c),
        calls: list.length,
        answers: ans,
        p50: median(ans),
        parts: { eot: md((t) => t.eotMs), llm: md((t) => t.ttftMs), tts: md((t) => t.ttsMs) },
        models: [...new Set(list.flatMap(({ m }) => m.models))],
        issues: list.reduce((n, { c, m }) => n + findingsFor(c, m).filter((f) => f.severity === "high" || f.severity === "medium").length, 0),
      }
    }
    const fleetRows = [
      ...fleet.map((a) => ({ key: a.key, name: a.name ?? "Unnamed agent", agent: a as Agent | null, ...stats(rowsFor(a.name ?? "unnamed")) })),
      { key: "other", name: "Agent not identified", agent: null as Agent | null, ...stats(rowsFor(null)) },
    ]

    return {
      fleetRows,
      calls,
      phone: calls.filter((c) => rooms.rooms.some((r) => r.sid === c.roomSid && r.participants.some((p) => p.kind === "sip"))).length,
      answers,
      p50: median(answers),
      perCall,
      byDay,
      parts,
      found,
      sev,
      fleet,
      failed,
      failedReal: failed.filter((x) => !TEST_AGENTS.has(x)).length,
      load,
      peak: Math.max(...load.map((x) => x.calls)),
      numbers: new Set(rows.filter((r) => !r.wildcard).map((r) => r.number)).size,
      plainLegs: legs.filter((l) => l === "RTP").length,
      legs: legs.length,
      sipFound,
      eg,
      lost: eg.filter((e) => e.status === "EGRESS_FAILED").length,
      ing,
      ingErr: ing.filter((i) => i.status === "ENDPOINT_ERROR").length,
      regions,
    }
  }, [])
}

function Section({ title, to, link, children, className }: { title: string; to?: string; link?: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("space-y-4 rounded-2xl border bg-background p-5", className)}>
      <div className="flex items-baseline justify-between">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {to && (
          <Link to={to} className="text-[12px] text-muted-foreground hover:text-foreground">
            {link ?? "Open"} →
          </Link>
        )}
      </div>
      {children}
    </section>
  )
}

function Stat({ icon: I, label, value, alert, to }: { icon: Icon; label: string; value: React.ReactNode; alert?: boolean; to: string }) {
  return (
    <Link to={to} className="group flex items-center gap-3 rounded-xl px-1 py-1 transition-colors hover:bg-muted/50">
      <span className={cn("flex size-9 items-center justify-center rounded-lg border bg-background", alert && "border-destructive/30 text-destructive")}>
        <I size={17} />
      </span>
      <span>
        <span className={cn("block text-[20px] leading-none font-semibold tracking-[-0.02em] tnum", alert && "text-destructive")}>{value}</span>
        <span className="mt-1 block text-[12px] text-muted-foreground">{label}</span>
      </span>
    </Link>
  )
}

export function OverviewPage() {
  const o = useOverview()
  const navigate = useNavigate()
  const [pick, setPick] = useUrlState("agent", "all")
  const [open, setOpen] = useState(false)
  const over = o.answers.filter((a) => a > BUDGET_MS).length
  const roleIcon: Record<string, Icon> = { stt: Microphone, llm: Brain, tts: SpeakerHigh }
  const lkHop = probe.endpoints.find((e) => e.role === "sfu")
  const highs = o.sev("high")
  const active = o.fleetRows.filter((r) => r.calls > 0)
  const idle = o.fleetRows.filter((r) => r.calls === 0)
  const chosen = pick === "all" ? active : active.filter((r) => r.key === pick)
  const shape = useMemo(() => anatomy(chosen.map((r) => ({ name: r.name, calls: r.callList }))), [pick, o])
  const wire = useMemo(() => wireData(o.fleetRows, BUDGET_MS), [o])
  const chainGroups = chosen.map((r) => ({ key: r.key, name: r.name, calls: r.callList, joins: r.agent ? joinDelays(r.agent.jobs) : [] }))

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.02em]">Overview</h1>
        <span className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <span className={cn("size-2 rounded-full", highs ? "bg-destructive" : "bg-good")} />
          {highs ? `${highs} high issues` : "No high issues"}
          <span className="text-border">|</span>
          {`${rooms.rooms.length} rooms watched · ${o.calls.length} calls captured`}
        </span>
      </header>

      <div className="grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
        <section className="flex flex-col items-center justify-center rounded-2xl border bg-background px-5 pt-4 pb-5">
          <AnswerGauge p50={o.p50} budget={BUDGET_MS} />
          <p className={cn("mt-2 text-[34px] leading-none font-semibold tracking-[-0.03em] tnum", o.p50 !== null && o.p50 > BUDGET_MS && "text-destructive")}>{o.p50 === null ? "Unknown" : fmtS(o.p50)}</p>
          <p className="mt-1.5 text-[12px] text-muted-foreground tnum">{`median answer · ${over} of ${o.answers.length} turns over ${BUDGET_MS / 1000} s`}</p>
        </section>
        <section className="flex flex-col justify-between gap-6 rounded-2xl border bg-background p-5">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat icon={PhoneCall} label={`calls · ${o.phone} by phone`} value={o.calls.length} to="/calls" />
            <Stat icon={WarningCircle} label={`issues · ${highs} high`} value={o.found.length} alert={highs > 0} to="/issues" />
            <Stat icon={Robot} label={`agents · ${active.filter((r) => r.agent).length} with calls`} value={o.fleet.length} to="/agents" />
            <Stat icon={Gauge} label="peak calls at once" value={o.peak} to="/agents" />
          </div>
          <div>
            <p className="pb-2 text-[13px] font-medium">Every turn</p>
            <TurnStrip
              dots={active.flatMap((r) =>
                r.callList.flatMap((c) =>
                  buildModel(c)
                    .turns.filter((t) => t.responseMs !== null)
                    .map((t) => ({ ms: t.responseMs!, room: c.room, turn: t.i, agent: r.name })),
                ),
              )}
              budget={BUDGET_MS}
            />
          </div>
        </section>
      </div>

      <Section title="Live wire">
        <LiveWire nodes={wire.nodes} links={wire.links} live={wire.live} trackers={wire.trackers} />
      </Section>

      <Section title="Turn anatomy">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <ToggleGroup type="single" variant="outline" size="sm" value={pick} onValueChange={(v) => v && setPick(v)} aria-label="Agent" className="max-w-full overflow-x-auto">
            <ToggleGroupItem value="all" className="px-3 data-[state=on]:bg-accent">
              All agents
            </ToggleGroupItem>
            {active.map((r) => (
              <ToggleGroupItem key={r.key} value={r.key} className="px-3 data-[state=on]:bg-accent">
                {r.name}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <span className="text-[12px] text-muted-foreground tnum">{`${shape.turns} turns`}</span>
        </div>
        <TurnAnatomy lanes={shape.lanes} e2e={shape.e2e} turns={shape.turns} budget={BUDGET_MS} />
        <Collapsible open={open} onOpenChange={setOpen}>
          <CollapsibleTrigger className="flex items-center gap-1.5 text-[13px] text-muted-foreground hover:text-foreground">
            <CaretRight size={12} className={cn("transition-transform", open && "rotate-90")} />
            Every stage, with where it comes from
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-4">
            <LatencyChain groups={chainGroups} />
          </CollapsibleContent>
        </Collapsible>
      </Section>

      <Section title="Agents" to="/agents" link="Agent workers">
        <div className="grid gap-4 md:grid-cols-2">
          {active.map((r) => {
            const peak = r.agent ? peakAtOnce(r.agent, rooms) : null
            const join = r.agent ? median(joinDelays(r.agent.jobs)) : null
            const failed = r.agent ? r.agent.jobs.filter((j) => j.failed).length : 0
            const perCall = r.callList.map((c) => ({
              room: c.room,
              p50: median(
                buildModel(c)
                  .turns.map((t) => t.responseMs)
                  .filter((v): v is number => v !== null),
              ),
            }))
            return (
              <button
                key={r.key}
                type="button"
                onClick={() => r.agent && navigate({ to: "/agents", search: { agent: r.key } as never })}
                className={cn("space-y-4 rounded-xl border p-4 text-left transition-colors", r.agent ? "hover:border-foreground/20 hover:bg-muted/30" : "cursor-default")}
              >
                <span className="flex items-start justify-between gap-3">
                  <span className="flex items-center gap-2.5">
                    <span className="flex size-9 items-center justify-center rounded-lg border bg-background">{r.agent ? r.agent.cloud ? <Cloud size={17} /> : <Robot size={17} /> : <Users size={17} />}</span>
                    <span>
                      <span className="block text-[14px] font-semibold">{r.name}</span>
                      <span className="block text-[11px] text-muted-foreground">
                        {r.agent ? (r.agent.cloud ? "LiveKit Cloud" : "self-hosted") : "console, local tests, untagged calls"}
                        {r.agent && TEST_AGENTS.has(r.name) ? " · our test agent" : ""}
                      </span>
                    </span>
                  </span>
                  <span className="flex gap-1.5 pt-1">
                    {[...new Set(r.models.map((m) => providerOfModel(m)).filter((x): x is string => x !== null))].map((prov) => (
                      <ProviderLogo key={prov} provider={prov} model={r.models.filter((m) => providerOfModel(m) === prov).join(", ")} size={16} />
                    ))}
                  </span>
                </span>
                <span className="flex items-end justify-between gap-4">
                  <span>
                    <span className={cn("block text-[30px] leading-none font-semibold tracking-[-0.03em] tnum", r.p50 !== null && r.p50 > BUDGET_MS && "text-destructive")}>{r.p50 === null ? "Unknown" : fmtS(r.p50)}</span>
                    <span className="mt-1 block text-[12px] text-muted-foreground">median answer</span>
                  </span>
                  <span className="grid grid-cols-3 gap-4 text-right">
                    {[
                      ["calls", String(r.calls)],
                      ["issues", String(r.issues + failed)],
                      ["at once", peak === null ? "unknown" : String(peak)],
                    ].map(([k, v]) => (
                      <span key={k}>
                        <span className={cn("block text-[15px] font-semibold tnum", k === "issues" && v !== "0" && "text-destructive", v === "unknown" && "text-[13px] font-normal text-muted-foreground")}>{v}</span>
                        <span className="block text-[11px] text-muted-foreground">{k}</span>
                      </span>
                    ))}
                  </span>
                </span>
                <span className="block space-y-1.5">
                  <CallTracker calls={perCall} budget={BUDGET_MS} />
                  <span className="flex justify-between text-[11px] text-muted-foreground">
                    <span>one block per call, by its median answer</span>
                    {join !== null && <span className="tnum">{`joins in ${join.toFixed(1)} s`}</span>}
                  </span>
                </span>
              </button>
            )
          })}
        </div>
        {idle.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            <span className="text-muted-foreground">No calls captured yet</span>
            {idle.map((r) => (
              <button key={r.key} type="button" onClick={() => r.agent && navigate({ to: "/agents", search: { agent: r.key } as never })} className="flex items-center gap-1.5 rounded-full border px-2.5 py-1 hover:bg-muted">
                {r.agent?.cloud ? <Cloud size={13} className="text-muted-foreground" /> : <Robot size={13} className="text-muted-foreground" />}
                <span className="font-medium">{r.name}</span>
                <span className="text-muted-foreground">{r.agent?.cloud ? (r.agent.cloud.deployment?.status ?? "Cloud") : `${r.agent?.jobs.length ?? 0} jobs seen`}</span>
              </button>
            ))}
          </div>
        )}
      </Section>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
        <Section title="LiveKit project" to="/project" link="Project">
          <div className="grid grid-cols-2 gap-3">
            {[
              { to: "/sfu", icon: HardDrives, label: "Media server", value: rooms.rooms.length, unit: "rooms", note: `${o.regions.map((r) => regionCity[r] ?? r).join(", ")}${lkHop ? ` · ${Math.round(lkHop.tcpMs)} ms` : ""}`, tone: "good" },
              {
                to: "/sip",
                icon: Phone,
                label: "Phone bridge",
                value: o.numbers,
                unit: "numbers",
                note: `${o.legs - o.plainLegs} of ${o.legs} legs encrypted`,
                tone: o.sipFound.some((c) => c.severity === "high") ? "bad" : o.plainLegs ? "warn" : "good",
              },
              { to: "/egress", icon: UploadSimple, label: "Egress", value: o.eg.length, unit: "jobs", note: o.lost ? `${o.lost} recordings lost` : "none lost", tone: o.lost ? "warn" : "good" },
              { to: "/ingress", icon: DownloadSimple, label: "Ingress", value: o.ing.length, unit: "endpoints", note: o.ingErr ? `${o.ingErr} in error` : "no errors", tone: o.ingErr ? "warn" : "good" },
            ].map((x) => (
              <Link key={x.to} to={x.to} className="rounded-xl border p-3 transition-colors hover:bg-muted/40">
                <span className="flex items-center justify-between text-[12px] text-muted-foreground">
                  <span className="flex items-center gap-1.5">
                    <x.icon size={14} /> {x.label}
                  </span>
                  <span className={cn("size-2 rounded-full", x.tone === "bad" ? "bg-destructive" : x.tone === "warn" ? "bg-amber-500" : "bg-good")} />
                </span>
                <span className="mt-1.5 flex items-baseline gap-1.5">
                  <span className="text-[22px] leading-none font-semibold tracking-[-0.02em] tnum">{x.value}</span>
                  <span className="text-[12px] text-muted-foreground">{x.unit}</span>
                </span>
                <span className={cn("mt-1.5 block truncate text-[11px]", x.tone === "good" ? "text-muted-foreground" : "text-amber-700 dark:text-amber-400")}>{x.note}</span>
              </Link>
            ))}
          </div>
        </Section>

        <Section title="Model providers" to="/project" link="Project">
          <div className="flex items-center gap-4">
            <div className="hidden shrink-0 overflow-hidden rounded-xl bg-muted/40 sm:block">
              <ProviderGlobe size={210} />
            </div>
            <ul className="min-w-0 flex-1 space-y-3">
              {probe.endpoints.map((e) => {
                const I = roleIcon[e.role] ?? HardDrives
                return (
                  <Tooltip key={e.role}>
                    <TooltipTrigger asChild>
                      <li className="grid cursor-help grid-cols-[18px_minmax(0,1fr)_52px] items-center gap-2.5">
                        <I size={15} className="text-muted-foreground" />
                        <span className="min-w-0">
                          <span className="flex justify-between gap-2 text-[12px]">
                            <span className="truncate font-medium">
                              {e.provider}
                              {e.model ? ` ${e.model}` : ""}
                            </span>
                            <span className="truncate text-muted-foreground">{e.place.split(",")[0].split(" (")[0]}</span>
                          </span>
                          <span className="mt-1 block h-1.5 rounded-full bg-muted">
                            <span className={cn("block h-full rounded-full", e.tcpMs > 50 ? "bg-destructive" : "bg-primary")} style={{ width: `${Math.max(Math.min(e.tcpMs, 100), 3)}%` }} />
                          </span>
                        </span>
                        <span className={cn("text-right text-[13px] font-semibold tnum", e.tcpMs > 50 && "text-destructive")}>{fmtMs(e.tcpMs)}</span>
                      </li>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-80 text-xs leading-relaxed">{`${e.host} · ${e.place} · located by ${e.located} · TCP connect ${fmtMs(e.tcpMs)}, TLS ${fmtMs(e.tlsMs)}, first byte ${fmtMs(e.ttfbMs)}${"measured" in e ? ` · ${(e as { measured: string }).measured}` : ""}`}</TooltipContent>
                  </Tooltip>
                )
              })}
              <li className="text-[11px] text-muted-foreground">Round trip from the Ontario host that ran vg-capture. Hover a row for when and how it was measured.</li>
            </ul>
          </div>
        </Section>
      </div>
    </div>
  )
}
