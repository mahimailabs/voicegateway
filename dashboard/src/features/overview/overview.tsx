import { useMemo, useState } from "react"
import { Link, useNavigate } from "@tanstack/react-router"
import { type UseQueryResult, useQuery } from "@tanstack/react-query"
import { Brain, CaretRight, Cloud, DownloadSimple, Gauge, HardDrives, Microphone, Phone, PhoneCall, Robot, SpeakerHigh, UploadSimple, Users, WarningCircle } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { ProviderLogo, providerOfModel } from "@/components/provider-logo"
import { QueryState, errorText } from "@/components/page-state"
import { presetRange } from "@/components/time-range-picker"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { ApiError, callStatsQuery, cloudAgentsQuery, egressQuery, ingressQuery, latestCallQuery, mediaServerQuery, providerProbesQuery, sipQuery, workersQuery } from "@/lib/api"
import type { CallStats, CloudAgents, Egress, Ingress, MediaServer, ProviderProbes, Sip, Workers } from "@/lib/api-types"
import { BUDGET_MS } from "@/lib/thresholds"
import { cn } from "@/lib/utils"
import { fmtMs } from "@/features/call/call-model"
import { type Agent, TEST_AGENTS, agentsFrom, joinDelays, median } from "@/features/agents/agents-model"
import { peakAtOnce } from "@/features/agents/agent-drawer"
import { type MediaServerData, fromContract, regionCity } from "@/features/media-server/room-drawer"
import { type SipData, checks as sipChecks, enc, routes } from "@/features/sip/sip-model"
import { ProviderGlobe } from "@/features/profile/provider-globe"
import { type ChainScope, LatencyChain } from "./latency-chain"
import { AnswerGauge, CallTracker, TurnAnatomy, TurnStrip, lanesFrom } from "./visuals"
import { LiveWire } from "./live-wire"
import { wireData } from "./wire-data"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useUrlState } from "@/lib/url-state"

// The whole stack at a glance: four numbers with their shape, the path a call takes with each part's
// state, where a turn's time goes, and where the models are. Every figure comes from the same data and
// checks as the page it links to; words are kept to labels.

export const fmtS = (ms: number) => `${(ms / 1000).toFixed(2)} s`

// Overview covers the seven days up to the project's latest call, the Calls page's default range.
// The range is fixed until the page has its own picker.
const RANGE = "7d"
export function useCallStats() {
  const latest = useQuery(latestCallQuery())
  const [opened] = useState(() => Date.now() / 1000)
  const last = latest.data ? Date.parse(latest.data.endedAt ?? latest.data.startedAt) / 1000 : latest.isSuccess ? opened : null
  const r = presetRange(RANGE, last ?? opened)
  // The picker's end is inclusive; the contract's is exclusive.
  const stats = useQuery({ ...callStatsQuery(new Date(r.from * 1000), new Date(r.to * 1000 + 1)), enabled: last !== null })
  // Until the latest call is read there is no range to ask for: its state stands for both.
  return last === null ? latest : stats
}

// The median answer with how many turns ran over the budget; Overview and the answer-time widget.
export function AnswerTimeFigure({ answers }: { answers: CallStats["answers"] }) {
  const p50 = answers.p50Ms
  const over = answers.overBudget === null || answers.turns === null ? "Unknown" : `${answers.overBudget} of ${answers.turns}`
  return (
    <>
      <AnswerGauge p50={p50} budget={answers.budgetMs} />
      <p className={cn("mt-2 text-[34px] leading-none font-semibold tracking-[-0.03em] tnum", p50 !== null && p50 > answers.budgetMs && "text-destructive")}>{p50 === null ? "Unknown" : fmtS(p50)}</p>
      <p className="mt-1.5 text-[12px] text-muted-foreground tnum">{`median answer · ${over} turns over ${answers.budgetMs / 1000} s`}</p>
    </>
  )
}

type StatsAgent = CallStats["agents"][number]
export type FleetRow = { key: string; name: string; agent: Agent | null; stats: StatsAgent | null }

// One row per agent the fleet knows plus one per agent the calls name that it does not, and one for
// calls no agent is identified on. Calls and figures come from getCallStats; the agent from the fleet.
function fleetRows(fleet: Agent[], stats: CallStats): FleetRow[] {
  // The API keys an agent by its registered name, "unnamed" when it has none, as the rooms report it.
  const byKey = new Map(stats.agents.map((a) => [a.key, a]))
  const keyOf = (a: Agent) => a.name ?? "unnamed"
  const rows: FleetRow[] = fleet.map((a) => ({ key: a.key, name: a.name ?? "Unnamed agent", agent: a, stats: byKey.get(keyOf(a)) ?? null }))
  for (const a of stats.agents)
    if (a.key !== null && !fleet.some((f) => keyOf(f) === a.key)) rows.push({ key: a.key, name: a.name ?? "Unnamed agent", agent: null, stats: a })
  rows.push({ key: "other", name: "Agent not identified", agent: null, stats: byKey.get(null) ?? null })
  return rows
}

const ms = (v: number | null) => (v === null ? "unknown" : fmtMs(v))
const plural = (n: number, one: string) => `${n} ${n === 1 ? one : `${one}s`}`
const sumOrNull = (xs: (number | null)[]) => (xs.some((x) => x === null) ? null : xs.reduce<number>((n, x) => n + (x ?? 0), 0))

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

export const NOT_SERVED = "Not served by this server yet"
export const NOT_COMPUTED = "Not computed yet"

// What a section says when the read it needs did not arrive: still loading, a read this server does
// not serve yet (404), or the error. Each section shows its own, so one missing read never blanks
// the page.
function readState(q: { isPending: boolean; isError: boolean; error: unknown }): string {
  if (q.isError) return q.error instanceof ApiError && q.error.status === 404 ? NOT_SERVED : errorText(q.error)
  return "Loading"
}

function Unavailable({ query, what }: { query: { isPending: boolean; isError: boolean; error: unknown }; what?: string }) {
  return (
    <p role="status" className="text-[13px] text-muted-foreground">
      {what ? `${what}: ${readState(query)}` : readState(query)}
    </p>
  )
}

export function OverviewPage() {
  const stats = useCallStats()
  const cloud = useQuery(cloudAgentsQuery())
  const media = useQuery(mediaServerQuery())
  const sip = useQuery(sipQuery())
  const egress = useQuery(egressQuery())
  const ingress = useQuery(ingressQuery())
  const workers = useQuery(workersQuery())
  const probes = useQuery(providerProbesQuery())
  // The call figures carry the page; without them the page says why, as before.
  if (!stats.data) return <QueryState icon={Gauge} what="the overview" query={stats} />
  return <OverviewView stats={stats.data as CallStats} cloud={cloud} media={media} sip={sip} egress={egress} ingress={ingress} workers={workers} probes={probes} />
}

type Reads = { cloud: UseQueryResult<CloudAgents>; media: UseQueryResult<MediaServer>; sip: UseQueryResult<Sip>; egress: UseQueryResult<Egress>; ingress: UseQueryResult<Ingress>; workers: UseQueryResult<Workers>; probes: UseQueryResult<ProviderProbes> }

function OverviewView({ stats, cloud: cloudRead, media, sip: sipQ, egress: egressQ, ingress: ingressQ, workers: workersQ, probes: probesQ }: { stats: CallStats } & Reads) {
  const navigate = useNavigate()
  const [pick, setPick] = useUrlState("agent", "all")
  const [open, setOpen] = useState(false)
  const rooms = useMemo<MediaServerData | null>(() => (media.data ? fromContract(media.data) : null), [media.data])
  const cloud = cloudRead.data
  // The fleet needs the agent workers and the media server; without them, the agent rows come from
  // the call figures alone.
  const fleet = useMemo(() => (cloud && rooms ? agentsFrom(cloud, rooms) : []), [cloud, rooms])
  const rows = useMemo(() => fleetRows(fleet, stats), [fleet, stats])
  const roleIcon: Record<string, Icon> = { stt: Microphone, llm: Brain, tts: SpeakerHigh }
  const probe = probesQ.data
  const lkHop = probe?.endpoints.find((e) => e.role === "sfu")
  const sipRead = sipQ.data
  const workers = workersQ.data
  const egress = egressQ.data
  const ingress = ingressQ.data
  const { answers, issueCounts } = stats
  const p50 = answers.p50Ms
  const highs = issueCounts.high
  const issueTotal = sumOrNull([issueCounts.high, issueCounts.medium, issueCounts.low, issueCounts.info])
  // An unknown call count keeps the agent's card, which says unknown; only a known zero is idle.
  const active = rows.filter((r) => r.stats && r.stats.callCount !== 0)
  const idle = rows.filter((r) => r.agent && (!r.stats || r.stats.callCount === 0))
  const chosen = pick === "all" ? null : (active.find((r) => r.key === pick) ?? null)
  const realtimeIn = (list: FleetRow[]) => list.filter((r) => r.stats?.stages?.some((s) => s.stage === "Realtime")).map((r) => r.name)
  // Stages null means this server does not compute turn anatomy yet, not that no stage was reported.
  const stages = chosen?.stats ? chosen.stats.stages : stats.stages
  const shape = chosen?.stats
    ? { lanes: chosen.stats.stages && lanesFrom(chosen.stats.stages, [chosen.name]), e2e: chosen.stats.answerP50Ms, turns: chosen.stats.anatomyTurns }
    : { lanes: stats.stages && lanesFrom(stats.stages, realtimeIn(active)), e2e: p50, turns: stats.anatomyTurns }
  const nameOf = (key: string | null) => rows.find((r) => r.key === (key ?? "other"))?.name ?? key ?? "Agent not identified"
  const dots = answers.values.flatMap((v) => (v.ms === null ? [] : [{ ms: v.ms, room: v.room, turn: v.turn, agent: nameOf(v.agentKey) }]))

  const sip = sipRead as unknown as SipData | undefined
  const wire = useMemo(() => (rooms && sip && probe ? wireData(rows, { stats, ms: rooms, sip, probes: probe }, BUDGET_MS) : null), [rows, stats, rooms, sip, probe])
  const wireMissing = ([["Media server", media], ["Phone bridge", sipQ], ["Provider probes", probesQ]] as const).find(([, q]) => !q.data)
  const chainAll: ChainScope | null = stats.chain && { key: "all", name: "All agents", chain: stats.chain, turns: stats.anatomyTurns, calls: stats.callCount, e2e: p50, joins: [] }
  const chainGroups: ChainScope[] = active
    .filter((r) => (pick === "all" || r.key === pick) && r.stats!.chain)
    .map((r) => ({ key: r.key, name: r.name, chain: r.stats!.chain!, turns: r.stats!.anatomyTurns, calls: r.stats!.callCount, e2e: r.stats!.answerP50Ms, joins: r.agent ? joinDelays(r.agent.jobs) : [] }))

  // Calls at once across the workers: only instants every worker reported count.
  const n = workers ? Math.min(...workers.workers.map((w) => w.samples.length)) : 0
  const load = Array.from({ length: Number.isFinite(n) ? n : 0 }, (_, i) => sumOrNull(workers!.workers.map((w) => w.samples[i].activeJobs)))
  const known = load.filter((x): x is number => x !== null)
  const peak = known.length ? Math.max(...known) : null

  const hints = rooms ? rooms.rooms.flatMap((r) => r.participants.filter((p) => p.sip).map((p) => ({ trunkId: p.sip!.trunkId, twilio: Boolean(p.sip!.twilio) }))) : []
  const routeRows = sip ? routes(sip, hints) : []
  const legs = sip ? [...sip.inbound.map((t) => enc(t.media.encryption)), ...sip.outbound.map((t) => enc(t.media.encryption))] : []
  const plainLegs = legs.filter((l) => l === "RTP").length
  const sipFound = sip ? sipChecks(sip, routeRows) : []
  const numbers = new Set(routeRows.filter((r) => !r.wildcard).map((r) => r.number)).size
  const lost = egress ? egress.egress.filter((e) => e.status === "EGRESS_FAILED").length : 0
  const ingErr = ingress ? ingress.ingress.filter((i) => i.status === "ENDPOINT_ERROR").length : 0
  const regions = rooms ? [...new Set(rooms.rooms.flatMap((r) => r.participants.map((p) => p.region)).filter((r): r is string => Boolean(r)))] : []
  const count = (v: number | null) => (v === null ? "Unknown" : v)

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.02em]">Overview</h1>
        <span className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <span className={cn("size-2 rounded-full", highs === null ? "bg-muted-foreground/40" : highs ? "bg-destructive" : "bg-good")} />
          {highs === null ? "High issues unknown" : highs ? `${highs} high issues` : "No high issues"}
          <span className="text-border">|</span>
          {`${rooms ? `${plural(rooms.rooms.length, "room")} watched · ` : ""}${stats.callCount === null ? "calls unknown" : `${plural(stats.callCount, "call")} captured`} in the last 7 days`}
        </span>
      </header>

      <div className="grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
        <section className="flex flex-col items-center justify-center rounded-2xl border bg-background px-5 pt-4 pb-5">
          <AnswerTimeFigure answers={answers} />
        </section>
        <section className="flex flex-col justify-between gap-6 rounded-2xl border bg-background p-5">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat icon={PhoneCall} label={`calls · ${count(stats.byChannel.phone)} by phone`} value={count(stats.callCount)} to="/calls" />
            <Stat icon={WarningCircle} label={`issues · ${count(highs)} high`} value={count(issueTotal)} alert={(highs ?? 0) > 0} to="/issues" />
            <Stat icon={Robot} label={`agents · ${active.filter((r) => r.agent).length} with calls`} value={cloud && rooms ? fleet.length : "Unknown"} to="/agents" />
            <Stat icon={Gauge} label="peak calls at once" value={count(peak)} to="/agents" />
          </div>
          {!workers && <Unavailable query={workersQ} what="Peak calls at once" />}
          <div>
            <p className="pb-2 text-[13px] font-medium">Every turn</p>
            <TurnStrip dots={dots} budget={BUDGET_MS} />
            {answers.valuesTruncated && <p className="pt-1 text-[11px] text-muted-foreground">{`The most recent ${answers.values.length} of ${count(answers.turns)} turns.`}</p>}
          </div>
        </section>
      </div>

      <Section title="Live wire">
        {wire ? <LiveWire nodes={wire.nodes} links={wire.links} live={wire.live} trackers={wire.trackers} /> : <Unavailable query={wireMissing ? wireMissing[1] : media} what={wireMissing ? wireMissing[0] : "Media server"} />}
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
          <span className="text-[12px] text-muted-foreground tnum">{`${count(shape.turns)} turns`}</span>
        </div>
        {shape.lanes && stages ? (
          <TurnAnatomy lanes={shape.lanes} e2e={shape.e2e} turns={shape.turns} budget={BUDGET_MS} />
        ) : (
          <p role="status" className="text-[13px] text-muted-foreground">{`Turn anatomy: ${NOT_COMPUTED}`}</p>
        )}
        <Collapsible open={open} onOpenChange={setOpen}>
          <CollapsibleTrigger className="flex items-center gap-1.5 text-[13px] text-muted-foreground hover:text-foreground">
            <CaretRight size={12} className={cn("transition-transform", open && "rotate-90")} />
            Every stage, with where it comes from
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-4">
            {chainAll ? <LatencyChain all={chainAll} groups={chainGroups} backMs={lkHop?.tcpMs ?? null} /> : <p role="status" className="text-[13px] text-muted-foreground">{`Every stage: ${NOT_COMPUTED}`}</p>}
          </CollapsibleContent>
        </Collapsible>
      </Section>

      <Section title="Agents" to="/agents" link="Agent workers">
        {!cloud && <Unavailable query={cloudRead} what="Agent workers" />}
        <div className="grid gap-4 md:grid-cols-2">
          {active.map((r) => {
            const peak = r.agent && rooms ? peakAtOnce(r.agent, rooms) : null
            const join = r.agent ? median(joinDelays(r.agent.jobs)) : null
            const failed = r.agent ? r.agent.jobs.filter((j) => j.failed).length : 0
            const st = r.stats!
            const p50 = st.answerP50Ms
            const perCall = st.calls.map((c) => ({ id: c.callId, room: c.room, p50: c.answerP50Ms }))
            return (
              // A card, not a button: it holds the strip's buttons, which open calls. The agent's name
              // opens the agent.
              <div key={r.key} className={cn("space-y-4 rounded-xl border p-4 text-left transition-colors", r.agent && "hover:border-foreground/20 hover:bg-muted/30")}>
                <span className="flex items-start justify-between gap-3">
                  <button
                    type="button"
                    disabled={!r.agent}
                    onClick={() => r.agent && navigate({ to: "/agents", search: { agent: r.key } as never })}
                    className={cn("flex items-center gap-2.5 text-left", !r.agent && "cursor-default")}
                  >
                    <span className="flex size-9 items-center justify-center rounded-lg border bg-background">{r.agent ? r.agent.cloud ? <Cloud size={17} /> : <Robot size={17} /> : <Users size={17} />}</span>
                    <span>
                      <span className="block text-[14px] font-semibold">{r.name}</span>
                      <span className="block text-[11px] text-muted-foreground">
                        {r.agent ? (r.agent.cloud ? "LiveKit Cloud" : "self-hosted") : r.key === "other" ? "console, local tests, untagged calls" : "worker details unknown"}
                        {r.agent && TEST_AGENTS.has(r.name) ? " · our test agent" : ""}
                      </span>
                    </span>
                  </button>
                  <span className="flex gap-1.5 pt-1">
                    {[...new Set(st.models.map((m) => providerOfModel(m)).filter((x): x is string => x !== null))].map((prov) => (
                      <ProviderLogo key={prov} provider={prov} model={st.models.filter((m) => providerOfModel(m) === prov).join(", ")} size={16} />
                    ))}
                  </span>
                </span>
                <span className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
                  <span>
                    <span className={cn("block text-[30px] leading-none font-semibold tracking-[-0.03em] tnum", p50 !== null && p50 > BUDGET_MS && "text-destructive")}>{p50 === null ? "Unknown" : fmtS(p50)}</span>
                    <span className="mt-1 block text-[12px] text-muted-foreground">median answer</span>
                  </span>
                  <span className="ml-auto flex flex-wrap justify-end gap-x-4 gap-y-2 text-right">
                    {[
                      ["calls", st.callCount === null ? "Unknown" : String(st.callCount)],
                      ["issues", st.issues === null ? "Unknown" : String(st.issues + failed)],
                      ["at once", peak === null ? "Unknown" : String(peak)],
                    ].map(([k, v]) => (
                      <span key={k} className="min-w-12">
                        <span className={cn("block text-[15px] font-semibold whitespace-nowrap tnum", k === "issues" && v !== "0" && v !== "Unknown" && "text-destructive", v === "Unknown" && "text-[13px] font-normal text-muted-foreground")}>{v}</span>
                        <span className="block text-[11px] text-muted-foreground">{k}</span>
                      </span>
                    ))}
                  </span>
                </span>
                <span className="block space-y-1.5">
                  <CallTracker calls={perCall} budget={BUDGET_MS} onOpen={(id) => navigate({ to: "/calls/$callId", params: { callId: id } })} />
                  <span className="flex justify-between text-[11px] text-muted-foreground">
                    <span>{st.callsTruncated ? `the most recent ${st.calls.length} calls, by their median answer` : "one block per call, by its median answer"}</span>
                    {join !== null && <span className="tnum">{`joins in ${join.toFixed(1)} s`}</span>}
                  </span>
                </span>
              </div>
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
              rooms
                ? { to: "/sfu", icon: HardDrives, label: "Media server", value: rooms.rooms.length, unit: "rooms", note: `${regions.map((r) => regionCity[r] ?? r).join(", ")}${lkHop?.tcpMs != null ? ` · ${Math.round(lkHop.tcpMs)} ms` : ""}`, tone: "good" }
                : { to: "/sfu", icon: HardDrives, label: "Media server", value: null, unit: "", note: readState(media), tone: "none" },
              sip
                ? {
                    to: "/sip",
                    icon: Phone,
                    label: "Phone bridge",
                    value: numbers,
                    unit: "numbers",
                    note: `${legs.length - plainLegs} of ${legs.length} legs encrypted`,
                    tone: sipFound.some((c) => c.severity === "high") ? "bad" : plainLegs ? "warn" : "good",
                  }
                : { to: "/sip", icon: Phone, label: "Phone bridge", value: null, unit: "", note: readState(sipQ), tone: "none" },
              egress
                ? { to: "/egress", icon: UploadSimple, label: "Egress", value: egress.egress.length, unit: "jobs", note: lost ? `${lost} recordings lost` : "none lost", tone: lost ? "warn" : "good" }
                : { to: "/egress", icon: UploadSimple, label: "Egress", value: null, unit: "", note: readState(egressQ), tone: "none" },
              ingress
                ? { to: "/ingress", icon: DownloadSimple, label: "Ingress", value: ingress.ingress.length, unit: "endpoints", note: ingErr ? `${ingErr} in error` : "no errors", tone: ingErr ? "warn" : "good" }
                : { to: "/ingress", icon: DownloadSimple, label: "Ingress", value: null, unit: "", note: readState(ingressQ), tone: "none" },
            ].map((x) => (
              <Link key={x.to} to={x.to} className="rounded-xl border p-3 transition-colors hover:bg-muted/40">
                <span className="flex items-center justify-between text-[12px] text-muted-foreground">
                  <span className="flex items-center gap-1.5">
                    <x.icon size={14} /> {x.label}
                  </span>
                  <span className={cn("size-2 rounded-full", x.tone === "bad" ? "bg-destructive" : x.tone === "warn" ? "bg-amber-500" : x.tone === "none" ? "bg-muted-foreground/40" : "bg-good")} />
                </span>
                <span className="mt-1.5 flex items-baseline gap-1.5">
                  <span className="text-[22px] leading-none font-semibold tracking-[-0.02em] tnum">{x.value ?? "Unknown"}</span>
                  <span className="text-[12px] text-muted-foreground">{x.unit}</span>
                </span>
                <span className={cn("mt-1.5 block truncate text-[11px]", x.tone === "good" || x.tone === "none" ? "text-muted-foreground" : "text-amber-700 dark:text-amber-400")}>{x.note}</span>
              </Link>
            ))}
          </div>
        </Section>

        <Section title="Model providers" to="/project" link="Project">
          {!probe ? (
            <Unavailable query={probesQ} />
          ) : (
          <div className="flex items-center gap-4">
            <div className="hidden shrink-0 overflow-hidden rounded-xl bg-muted/40 sm:block">
              <ProviderGlobe probes={probe} size={210} />
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
                            {e.tcpMs !== null && <span className={cn("block h-full rounded-full", e.tcpMs > 50 ? "bg-destructive" : "bg-primary")} style={{ width: `${Math.max(Math.min(e.tcpMs, 100), 3)}%` }} />}
                          </span>
                        </span>
                        <span className={cn("text-right text-[13px] font-semibold tnum", e.tcpMs !== null && e.tcpMs > 50 && "text-destructive", e.tcpMs === null && "font-normal text-muted-foreground")}>{ms(e.tcpMs)}</span>
                      </li>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-80 text-xs leading-relaxed">{`${e.host} · ${e.place} · located by ${e.located} · TCP connect ${ms(e.tcpMs)}, TLS ${ms(e.tlsMs)}, first byte ${ms(e.ttfbMs)}${e.measured ? ` · ${e.measured}` : ""}`}</TooltipContent>
                  </Tooltip>
                )
              })}
              <li className="text-[11px] text-muted-foreground">Round trip from the Ontario host that ran vg-capture. Hover a row for when and how it was measured.</li>
            </ul>
          </div>
          )}
        </Section>
      </div>
    </div>
  )
}
