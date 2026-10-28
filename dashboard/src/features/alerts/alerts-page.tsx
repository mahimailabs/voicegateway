import { useMemo } from "react"
import { useHosted } from "@/lib/hosted"
import { Link } from "@tanstack/react-router"
import { Bell, BellRinging, EnvelopeSimple, Plugs, ShieldWarning, WebhooksLogo } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { Bar, BarChart, XAxis } from "recharts"
import { ProviderLogo } from "@/components/provider-logo"
import { type ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { CHANNELS, type Channel, type Event, meta, type Rule, type RuleId, channelName, delivers, failing, firingByRule, ruleSetup, rulesFrom, type Setup, setupFrom, useSetRule } from "./alerts-model"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { errorText, QueryState } from "@/components/page-state"
import { alertEventsQuery, alertIncidentsQuery, alertSetupQuery, resolveAlertIncident } from "@/lib/api"
import type { AlertChannel, AlertDelivery, AlertIncident } from "@/lib/api-types"
import { currentScope, useUrlState } from "@/lib/url-state"
import { roleIn, useSession } from "@/lib/session"

// Alerts: the console's checks, routed to where the team already works (decision 0020). Counts are
// what each rule would have fired on in the captured data; the worker groups those into incidents
// (listAlertIncidents) and sends each change to the rule's channels.

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
function RuleCard({ r, days, setup, firing, channels, canEdit }: { r: Rule; days: number[]; setup: Setup; firing: AlertIncident[]; channels: AlertChannel[]; canEdit: boolean }) {
  const setRule = useSetRule()
  const s = ruleSetup(setup, r.id)
  const id = r.id as RuleId
  const notDelivered = failing(channels).filter((c) => s.on && s.to.includes(c.kind))
  const connected = new Set<Channel>(channels.map((c) => c.kind))
  const perDay = days.map((d) => r.events.filter((e) => within(e.at, d)).length)
  const peak = Math.max(1, ...perDay)
  return (
    <div className={cn("relative overflow-hidden rounded-2xl border bg-background p-4 pl-5 transition-opacity", !s.on && "opacity-60")}>
      <span className={cn("absolute inset-y-0 left-0 w-1", sevTone[r.severity])} />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-semibold">{r.title}</span>
            {(r.state || firing.length > 0) && (
              <span className="flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-400">
                <span className="size-1.5 animate-pulse rounded-full bg-amber-500" />
                {firing.length > 1 ? `Firing for ${firing.length}` : firing.length ? "Firing" : "Active now"}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-[12px] text-muted-foreground">{r.condition}</p>
        </div>
        <Switch className="data-[state=checked]:bg-foreground" checked={s.on} disabled={!canEdit} onCheckedChange={(v) => setRule(setup, id, { on: v })} aria-label={`${r.title} on`} />
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

      {firing.length > 0 && (
        <ul className="mt-3 space-y-1">
          {firing.slice(0, 3).map((i) => (
            <li key={i.id} className="text-[12px] text-muted-foreground">
              <span className="font-medium text-foreground">{i.subject}</span>
              {` · ${i.occurrences ?? "unknown"} ${i.occurrences === 1 ? "occurrence" : "occurrences"} since ${time(Date.parse(i.openedAt) / 1000)}`}
              {i.holdReason && ` · held: ${i.holdReason}`}
              {i.silentSince && ` · no data since ${time(Date.parse(i.silentSince) / 1000)}`}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-3">
        <span className={cn("text-[11px]", notDelivered.length ? "text-destructive" : "text-muted-foreground")}>
          {notDelivered.length ? `Not delivered to ${notDelivered.map((c) => channelName[c.kind]).join(", ")}` : s.to.length ? `Sends to ${s.to.map((c) => channelName[c]).join(", ")}` : "Not sending yet"}
        </span>
        <ToggleGroup type="multiple" size="sm" value={s.to} disabled={!canEdit} onValueChange={(v) => setRule(setup, id, { to: v as Channel[] })} aria-label={`Channels for ${r.title}`} className="gap-0.5">
          {CHANNELS.map((c) => (
            <Tooltip key={c}>
              <TooltipTrigger asChild>
                {/* A disabled button takes no pointer events, so the span carries the tooltip. */}
                <span tabIndex={delivers(c) ? -1 : 0}>
                  <ToggleGroupItem
                    value={c}
                    disabled={!delivers(c)}
                    aria-label={channelName[c]}
                    className="size-7 rounded-md p-0 opacity-45 grayscale aria-pressed:bg-accent aria-pressed:opacity-100 aria-pressed:grayscale-0 aria-pressed:ring-1 aria-pressed:ring-border"
                  >
                    <ChannelMark c={c} />
                  </ToggleGroupItem>
                </span>
              </TooltipTrigger>
              <TooltipContent className="text-xs">{delivers(c) ? (connected.has(c) ? channelName[c] : `${channelName[c]}: set it up in Settings first`) : `${channelName[c]}: coming soon`}</TooltipContent>
            </Tooltip>
          ))}
        </ToggleGroup>
      </div>
    </div>
  )
}

const changeName = { firing: "Firing", reminder: "Reminder", resolved: "Resolved", test: "Test" }
const deliveryTone = { delivered: "text-emerald-700 dark:text-emerald-400", pending: "text-muted-foreground", retrying: "text-amber-700 dark:text-amber-400", failed: "text-destructive" }

// One delivery of an incident's change: where, its state, and the last answer when it was not delivered.
function DeliveryLine({ d }: { d: AlertDelivery }) {
  return (
    <li className="text-[12px]">
      <span className="text-muted-foreground">{`${changeName[d.change]} to ${channelName[d.channel]}: `}</span>
      <span className={deliveryTone[d.state]}>{d.state === "delivered" ? "delivered" : d.state === "failed" ? "not delivered" : d.state}</span>
      {d.state !== "delivered" && d.status && <span className="text-muted-foreground">{` · ${d.status}${d.message ? `, ${d.message}` : ""}`}</span>}
    </li>
  )
}

// History: the incidents the worker opened, newest first, grouped by the day each opened, with every
// delivery of their changes.
function Incidents({ list, canResolve }: { list: AlertIncident[]; canResolve: boolean }) {
  const qc = useQueryClient()
  const resolve = useMutation({
    mutationFn: (id: string) => resolveAlertIncident(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: alertIncidentsQuery().queryKey }),
    onError: (e: Error) => toast.error(`Could not resolve: ${errorText(e)}`),
  })
  if (!list.length) return <p className="rounded-2xl border border-dashed px-4 py-8 text-center text-[13px] text-muted-foreground">No incident yet. When a rule matches, the worker opens one here and sends it to the rule's channels.</p>
  const opened = (i: AlertIncident) => Date.parse(i.openedAt) / 1000
  const groups = [...list.reduce((m, i) => m.set(day(opened(i)), [...(m.get(day(opened(i))) ?? []), i]), new Map<string, AlertIncident[]>())]
  return (
    <div className="space-y-6">
      {groups.map(([d, items]) => (
        <section key={d}>
          <p className="pb-2 text-[12px] font-medium text-muted-foreground">{d}</p>
          <ol className="relative ml-1.5 space-y-1 border-l pl-5">
            {items.map((i) => {
              const m = meta(i.ruleId)
              const last = i.lastOccurrence
              return (
                <li key={i.id} className="relative rounded-xl px-3 py-2.5 hover:bg-muted/40">
                  <span className={cn("absolute top-4 -left-[25px] size-2.5 rounded-full ring-4 ring-background", i.state === "firing" ? sevTone[m.severity] : "bg-muted-foreground/40")} />
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[13px] font-medium">{`${m.title} · ${i.subject}`}</span>
                    <span className="shrink-0 text-[11px] text-muted-foreground tnum">{`${time(opened(i))}${i.resolvedAt ? ` to ${time(Date.parse(i.resolvedAt) / 1000)}` : ""}`}</span>
                  </div>
                  <p className="text-[13px] text-muted-foreground">
                    {i.state === "firing" ? "Firing" : i.resolvedBy === "person" ? "Resolved by a person" : "Resolved"}
                    {` · ${i.occurrences ?? "unknown"} ${i.occurrences === 1 ? "occurrence" : "occurrences"}`}
                    {i.holdReason && ` · held: ${i.holdReason}`}
                    {i.silentSince && ` · no data since ${time(Date.parse(i.silentSince) / 1000)}`}
                  </p>
                  <p className="text-[13px] text-muted-foreground">
                    {`Last: ${last.detail} · `}
                    {last.callId ? (
                      <Link to="/calls/$callId" params={{ callId: last.callId }} className="font-mono hover:text-foreground hover:underline">
                        {last.where}
                      </Link>
                    ) : (
                      <span className="font-mono">{last.where}</span>
                    )}
                  </p>
                  {i.deliveries.length > 0 && <ul className="mt-1 space-y-0.5">{i.deliveries.map((x) => <DeliveryLine key={x.id} d={x} />)}</ul>}
                  {canResolve && i.state === "firing" && i.ruleId === "recording-lost" && (
                    <Button size="sm" variant="outline" className="mt-2" disabled={resolve.isPending} onClick={() => resolve.mutate(i.id)}>
                      Mark resolved
                    </Button>
                  )}
                </li>
              )
            })}
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
  const incidents = useQuery(alertIncidentsQuery())
  if (!list) return <QueryState icon={Bell} what="alerts" query={read} />
  if (!setup || !saved.data) return <QueryState icon={Bell} what="alert setup" query={saved} />
  return <AlertsView list={list} setup={setup} channels={saved.data.channels} incidents={incidents.data?.items ?? null} incidentsError={incidents.isError} />
}

function AlertsView({ list, setup, channels, incidents, incidentsError }: { list: Rule[]; setup: Setup; channels: AlertChannel[]; incidents: AlertIncident[] | null; incidentsError: boolean }) {
  // "On the server" is said only on a self-hosted install; Cloud users have no server.
  const selfHosted = useHosted() === "self-hosted"
  const role = roleIn(currentScope().org, useSession())
  const canEdit = role === "Admin" || role === "Member"
  const [tab, setTab] = useUrlState("tab", "rules")
  const firing = firingByRule(incidents ?? [])
  const firingCount = (incidents ?? []).filter((i) => i.state === "firing").length
  const on = list.filter((r) => ruleSetup(setup, r.id).on)
  const events: Fired[] = on.flatMap((r) => r.events.map((e) => ({ ...e, rule: r }))).sort((a, b) => (a.at === null ? 1 : b.at === null ? -1 : b.at - a.at))
  const routed = on.filter((r) => ruleSetup(setup, r.id).to.length > 0).length
  const highs = events.filter((e) => e.rule.severity === "high").length
  const active = list.filter((r) => r.state || firing[r.id as RuleId])

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
          <p className="text-[13px] text-muted-foreground">{channels.length ? "The checks this console runs, sent where your team works." : `The checks this console runs, sent where your team works. Nothing is sent until ${selfHosted ? "an Admin sets up email or a webhook" : "you set up email or a webhook"}.`}</p>
        </div>
        <Link to="/alerts" search={{ settings: "integrations" } as never} className="inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-[13px] font-medium hover:bg-muted">
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
          <Stat icon={ShieldWarning} label={active.map((r) => (r.state ? `${r.title}: ${r.state}` : `${r.title}: firing`)).join(" · ") || (incidentsError ? "incidents could not be read" : "nothing ongoing")} value={incidents === null && incidentsError ? "unknown" : active.length} alert={active.length > 0} />
          <Stat icon={BellRinging} label={failing(channels).length ? `channels set up · ${failing(channels).map((c) => channelName[c.kind]).join(", ")} not delivering` : "channels set up"} value={`${channels.length} of 2`} alert={failing(channels).length > 0}>
            <span className="mt-1.5 flex gap-1.5">
              {(["email", "webhook"] as const).map((c) => (
                <span key={c} className={cn(!channels.some((x) => x.kind === c) && "opacity-40 grayscale")}>
                  <ChannelMark c={c} />
                </span>
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
            History <span className="ml-1 text-[11px] text-muted-foreground tnum">{incidents ? (firingCount ? `${firingCount} firing` : incidents.length) : ""}</span>
          </TabsTrigger>
        </TabsList>
        <TabsContent value="rules" className="space-y-3">
          <div className="grid gap-4 md:grid-cols-2">
            {[...list]
              .sort((a, b) => Number(Boolean(b.state)) - Number(Boolean(a.state)) || (a.severity === b.severity ? b.events.length - a.events.length : a.severity === "high" ? -1 : 1))
              .map((r) => (
                <RuleCard key={r.id} r={r} days={days} setup={setup} firing={firing[r.id as RuleId] ?? []} channels={channels} canEdit={canEdit} />
              ))}
          </div>
          <p className="text-[12px] text-muted-foreground">{`Thresholds come from Settings, so a rule and the page it watches always agree. A rule that is off still opens incidents but sends nothing.${canEdit ? "" : " Only a Member or Admin changes rules."}`}</p>
        </TabsContent>
        <TabsContent value="history">
          {incidents ? <Incidents list={incidents} canResolve={canEdit} /> : <p className="rounded-2xl border border-dashed px-4 py-8 text-center text-[13px] text-muted-foreground">{incidentsError ? "Incidents could not be read." : "Loading incidents"}</p>}
        </TabsContent>
      </Tabs>
    </div>
  )
}
