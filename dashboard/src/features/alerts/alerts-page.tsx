import { useMemo } from "react"
import { Link } from "@tanstack/react-router"
import { ArrowSquareOut, Bell, BellRinging, EnvelopeSimple, Plugs, ShieldWarning, WebhooksLogo } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { Bar, BarChart, XAxis } from "recharts"
import { ProviderLogo } from "@/components/provider-logo"
import { type ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { CHANNELS, type Channel, type Event, type Rule, channelName, ruleSetup, rulesFrom, type Setup, setupFrom, useSetRule } from "./alerts-model"
import { useQuery } from "@tanstack/react-query"
import { QueryState } from "@/components/page-state"
import { alertEventsQuery, alertSetupQuery } from "@/lib/api"
import { useUrlState } from "@/lib/url-state"

// Alerts: the console's checks, routed to where the team already works (decision 0020). Counts are
// what each rule would have fired on in the captured data; nothing is sent until the Phase 2 worker
// runs and the channels are connected on the server.

function ChannelMark({ c, size = 14 }: { c: Channel; size?: number }) {
  if (c === "email") return <EnvelopeSimple size={size} />
  if (c === "webhook") return <WebhooksLogo size={size} />
  return <ProviderLogo provider={c} size={size} />
}

// An event with no reported time is listed under "Time unknown", never placed on a date.
const day = (s: number | null) => (s === null ? "Time unknown" : new Date(s * 1000).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }))
const time = (s: number | null) => (s === null ? "unknown" : new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))
const within = (t: number | null, d: number) => t !== null && t >= d && t < d + 86400
const sevTone = { high: "bg-destructive", medium: "bg-amber-500" }
const sevText = { high: "text-destructive", medium: "text-amber-700 dark:text-amber-400" }
const chartConfig = { high: { label: "High", color: "var(--destructive)" }, medium: { label: "Medium", color: "oklch(0.78 0.15 75)" } } satisfies ChartConfig

type Fired = Event & { rule: Rule }

function Stat({ icon: I, label, value, children, alert }: { icon: Icon; label: string; value: React.ReactNode; children?: React.ReactNode; alert?: boolean }) {
  return (
    <div className="flex items-start gap-3">
      <span className={cn("flex size-9 shrink-0 items-center justify-center rounded-lg border bg-background", alert && "border-destructive/30 text-destructive")}>
        <I size={17} />
      </span>
      <span className="min-w-0">
        <span className={cn("block text-[20px] leading-none font-semibold tracking-[-0.02em] tnum", alert && "text-destructive")}>{value}</span>
        <span className="mt-1 block text-[12px] text-muted-foreground">{label}</span>
        {children}
      </span>
    </div>
  )
}

// One card per rule: severity rail, on switch, how often it would have fired with a per-day strip, and
// where it sends.
function RuleCard({ r, days, setup }: { r: Rule; days: number[]; setup: Setup }) {
  const setRule = useSetRule()
  const s = ruleSetup(setup, r.id)
  const perDay = days.map((d) => r.events.filter((e) => within(e.at, d)).length)
  const peak = Math.max(1, ...perDay)
  return (
    <div className={cn("relative overflow-hidden rounded-2xl border bg-background p-4 pl-5 transition-opacity", !s.on && "opacity-60")}>
      <span className={cn("absolute inset-y-0 left-0 w-1", sevTone[r.severity])} />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-semibold">{r.title}</span>
            {r.state && (
              <span className="flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-400">
                <span className="size-1.5 animate-pulse rounded-full bg-amber-500" />
                Active now
              </span>
            )}
          </div>
          <p className="mt-0.5 text-[12px] text-muted-foreground">{r.condition}</p>
        </div>
        <Switch className="data-[state=checked]:bg-foreground" checked={s.on} onCheckedChange={(v) => setRule(r.id, { on: v })} aria-label={`${r.title} on`} />
      </div>

      <div className="mt-4 flex items-end justify-between gap-4">
        <div>
          <p className={cn("text-[26px] leading-none font-semibold tracking-[-0.02em] tnum", r.events.length || r.state ? sevText[r.severity] : "")}>{r.state ? r.state : r.events.length}</p>
          <p className="mt-1 text-[11px] text-muted-foreground">{r.state ? `ongoing · ${r.source}` : `would have fired · ${r.source}`}</p>
        </div>
        {!r.state && days.length > 0 && (
          <div className="flex h-8 items-end gap-[3px]" role="img" aria-label="Fired per day">
            {perDay.map((n, k) => (
              <Tooltip key={k}>
                <TooltipTrigger asChild>
                  <span className={cn("w-3 rounded-[2px]", n ? sevTone[r.severity] : "bg-muted")} style={{ height: n ? `${25 + (n / peak) * 75}%` : "18%" }} />
                </TooltipTrigger>
                <TooltipContent className="text-xs">{`${day(days[k])}: ${n || "none"}`}</TooltipContent>
              </Tooltip>
            ))}
          </div>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-3">
        <span className="text-[11px] text-muted-foreground">{s.to.length ? `Sends to ${s.to.map((c) => channelName[c]).join(", ")}` : "Not sending yet"}</span>
        <ToggleGroup type="multiple" size="sm" value={s.to} onValueChange={(v) => setRule(r.id, { to: v as Channel[] })} aria-label={`Channels for ${r.title}`} className="gap-0.5">
          {CHANNELS.map((c) => (
            <Tooltip key={c}>
              <TooltipTrigger asChild>
                <ToggleGroupItem
                  value={c}
                  aria-label={channelName[c]}
                  className="size-7 rounded-md p-0 opacity-45 grayscale aria-pressed:bg-accent aria-pressed:opacity-100 aria-pressed:grayscale-0 aria-pressed:ring-1 aria-pressed:ring-border"
                >
                  <ChannelMark c={c} />
                </ToggleGroupItem>
              </TooltipTrigger>
              <TooltipContent className="text-xs">{`${channelName[c]}: not connected yet`}</TooltipContent>
            </Tooltip>
          ))}
        </ToggleGroup>
      </div>
    </div>
  )
}

// History as a timeline, newest first, grouped by day.
function Timeline({ events }: { events: Fired[] }) {
  if (!events.length) return <p className="rounded-2xl border border-dashed px-4 py-8 text-center text-[13px] text-muted-foreground">No rule would have fired on the captured data.</p>
  const groups = [...events.reduce((m, e) => m.set(day(e.at), [...(m.get(day(e.at)) ?? []), e]), new Map<string, Fired[]>())]
  return (
    <div className="space-y-6">
      {groups.map(([d, list]) => (
        <section key={d}>
          <p className="pb-2 text-[12px] font-medium text-muted-foreground">{d}</p>
          <ol className="relative ml-1.5 space-y-1 border-l pl-5">
            {list.map((e, k) => (
              <li key={k} className="relative rounded-xl px-3 py-2.5 hover:bg-muted/40">
                <span className={cn("absolute top-4 -left-[25px] size-2.5 rounded-full ring-4 ring-background", sevTone[e.rule.severity])} />
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-[13px] font-medium">{e.rule.title}</span>
                  <span className="text-[11px] text-muted-foreground tnum">{time(e.at)}</span>
                </div>
                <p className="text-[13px] text-muted-foreground">{e.detail}</p>
                {e.link && (
                  <span className="mt-0.5 inline-flex items-center gap-1 text-[12px]">
                    {e.link.to === "/calls/$callId" ? (
                      <Link to="/calls/$callId" params={{ callId: e.link.callId }} className="font-mono text-muted-foreground hover:text-foreground hover:underline">
                        {e.where}
                      </Link>
                    ) : (
                      <Link to={e.link.to} className="font-mono text-muted-foreground hover:text-foreground hover:underline">
                        {e.where}
                      </Link>
                    )}
                    <ArrowSquareOut size={11} className="text-muted-foreground" />
                  </span>
                )}
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  )
}

export function AlertsPage() {
  const read = useQuery(alertEventsQuery())
  const list = useMemo(() => (read.data ? rulesFrom(read.data) : null), [read.data])
  const saved = useQuery(alertSetupQuery())
  const setup = useMemo(() => (saved.data ? setupFrom(saved.data) : null), [saved.data])
  if (!list) return <QueryState icon={Bell} what="alerts" query={read} />
  if (!setup) return <QueryState icon={Bell} what="alert setup" query={saved} />
  return <AlertsView list={list} setup={setup} />
}

function AlertsView({ list, setup }: { list: Rule[]; setup: Setup }) {
  const [tab, setTab] = useUrlState("tab", "rules")
  const on = list.filter((r) => ruleSetup(setup, r.id).on)
  const events: Fired[] = on.flatMap((r) => r.events.map((e) => ({ ...e, rule: r }))).sort((a, b) => (a.at === null ? 1 : b.at === null ? -1 : b.at - a.at))
  const routed = on.filter((r) => ruleSetup(setup, r.id).to.length > 0).length
  const highs = events.filter((e) => e.rule.severity === "high").length
  const active = list.filter((r) => r.state)

  // Days from the first to the last event, so every rule's strip and the chart share one axis.
  const dated = list.flatMap((r) => r.events.flatMap((e) => (e.at === null ? [] : [e.at])))
  const start = dated.length ? new Date(new Date(Math.min(...dated) * 1000).setHours(0, 0, 0, 0)).getTime() / 1000 : 0
  const end = dated.length ? Math.max(...dated) : 0
  const days = dated.length ? Array.from({ length: Math.max(1, Math.ceil((end - start) / 86400)) }, (_, i) => start + i * 86400) : []
  const series = days.map((d) => ({
    day: new Date(d * 1000).toLocaleDateString([], { month: "short", day: "numeric" }),
    high: events.filter((e) => e.rule.severity === "high" && within(e.at, d)).length,
    medium: events.filter((e) => e.rule.severity === "medium" && within(e.at, d)).length,
  }))

  return (
    <div className="mx-auto max-w-[1200px] space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.02em]">Alerts</h1>
          <p className="text-[13px] text-muted-foreground">The checks this console runs, sent where your team works. Nothing is sent until a channel connects on the server.</p>
        </div>
        <Link to="/settings" className="inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-[13px] font-medium hover:bg-muted">
          <Plugs size={15} /> Set up channels
        </Link>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <section className="rounded-2xl border bg-background p-5">
          <div className="flex items-baseline justify-between">
            <p className="text-[13px] font-medium">Would have fired</p>
            <p className="text-[12px] text-muted-foreground tnum">{`${events.length} alerts · ${highs} high`}</p>
          </div>
          <ChartContainer config={chartConfig} className="mt-3 aspect-auto h-40 w-full">
            <BarChart data={series} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
              <XAxis dataKey="day" tickLine={false} axisLine={false} tickMargin={6} />
              <ChartTooltip cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={<ChartTooltipContent />} />
              <Bar dataKey="high" stackId="a" fill="var(--color-high)" radius={[0, 0, 4, 4]} isAnimationActive={false} />
              <Bar dataKey="medium" stackId="a" fill="var(--color-medium)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ChartContainer>
        </section>
        <section className="grid grid-cols-1 content-center gap-5 rounded-2xl border bg-background p-5">
          <Stat icon={Bell} label={routed ? `rules on · ${routed} sending to a channel` : "rules on · none sending yet"} value={`${on.length} of ${list.length}`} />
          <Stat icon={ShieldWarning} label={active.map((r) => `${r.title}: ${r.state}`).join(" · ") || "nothing ongoing"} value={active.length} alert={active.length > 0} />
          <Stat icon={BellRinging} label="channels connected" value={`0 of ${CHANNELS.length}`}>
            <span className="mt-1.5 flex gap-1.5 opacity-60 grayscale">
              {CHANNELS.map((c) => (
                <ChannelMark key={c} c={c} />
              ))}
            </span>
          </Stat>
        </section>
      </div>

      <Tabs value={tab} onValueChange={setTab} className="gap-5">
        <TabsList>
          <TabsTrigger value="rules">
            Rules <span className="ml-1 text-[11px] text-muted-foreground tnum">{list.length}</span>
          </TabsTrigger>
          <TabsTrigger value="history">
            History <span className="ml-1 text-[11px] text-muted-foreground tnum">{events.length}</span>
          </TabsTrigger>
        </TabsList>
        <TabsContent value="rules" className="space-y-3">
          <div className="grid gap-4 md:grid-cols-2">
            {[...list]
              .sort((a, b) => Number(Boolean(b.state)) - Number(Boolean(a.state)) || (a.severity === b.severity ? b.events.length - a.events.length : a.severity === "high" ? -1 : 1))
              .map((r) => (
                <RuleCard key={r.id} r={r} days={days} setup={setup} />
              ))}
          </div>
          <p className="text-[12px] text-muted-foreground">Thresholds come from Settings, so a rule and the page it watches always agree. Your choices are kept in this browser until the server stores them.</p>
        </TabsContent>
        <TabsContent value="history">
          <Timeline events={events} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
