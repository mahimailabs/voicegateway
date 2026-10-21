import { useMemo, useState } from "react"
import { useNavigate, useParams, useSearch } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { ArrowLeft } from "@phosphor-icons/react"
import { Bar, BarChart, XAxis } from "recharts"
import mark from "@/assets/brand/mark.svg"
import { Card } from "@/components/ui/card"
import { type ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { Item, ItemActions, ItemContent, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item"
import { CategoryBar } from "@/components/tremor/CategoryBar"
import { type AvailableChartColorsKeys, getColorClassName } from "@/components/tremor/chartColors"
import { ApiError, callQuery, callStatsQuery, fetchCallPage, issuesInQuery } from "@/lib/api"
import type { CallStats, CallSummary } from "@/lib/api-types"
import { embedToken, sharesContent } from "@/lib/embed"
import { cn } from "@/lib/utils"
import { fmtMs } from "@/features/call/call-model"
import { CallView } from "@/features/call/call-view"
import { issuesFrom, type Severity } from "@/features/issues/issues-model"
import { AnswerGauge } from "@/features/overview/visuals"
import { type Size, type WidgetId, widgets } from "./widgets"

// One widget in a frame on someone else's site (decisions 0023, 0034): a compact tile in an iPhone
// widget size, no console shell, links that stay inside the widgets. It reads with the embed token in
// its address; in the console's own preview, with the signed-in session.

const DAY = 86_400_000
const sevDot: Record<Severity, string> = { high: "bg-destructive", medium: "bg-amber-500", low: "bg-muted-foreground/50", info: "bg-muted-foreground/30" }
const stageColors: AvailableChartColorsKeys[] = ["gray", "cyan", "blue", "violet", "amber", "emerald", "pink", "lime"]
const count = (n: number | null | undefined) => (n === null || n === undefined ? "Unknown" : n.toLocaleString("en"))

// Every widget covers the seven days up to the moment the frame opened, so its figures agree.
function useRange() {
  const [to] = useState(() => new Date(Math.ceil(Date.now() / 60_000) * 60_000))
  return { from: new Date(to.getTime() - 7 * DAY), to }
}
function useStats() {
  const { from, to } = useRange()
  return useQuery({ ...callStatsQuery(from, to), refetchInterval: 60_000 })
}

// What a tile says when its read did not arrive. 401 is a token that expired, was revoked, or whose
// site is no longer allowed: the host page mints a new one when it loads again.
function stateOf(q: { isPending: boolean; error: unknown }): string {
  if (q.error instanceof ApiError && q.error.status === 401) return embedToken() ? "This widget's access has ended. Reload the page." : "Sign in to see this widget."
  if (q.error instanceof ApiError && q.error.status === 403) return "This widget's token does not open this view."
  return q.error ? "Could not be loaded." : "Loading."
}

function Tile({ title, size, children, note }: { title: string; size: Size; children: React.ReactNode; note?: string }) {
  return (
    <Card size="sm" className={cn("h-svh rounded-none py-3 ring-0", size === "small" ? "gap-1.5" : "gap-2.5")}>
      <div className="flex items-center justify-between px-3.5 text-[12px] font-semibold text-muted-foreground">
        <span className="truncate">{title}</span>
        <a href="https://voicegateway.dev" target="_blank" rel="noopener noreferrer" aria-label="Powered by VoiceGateway" className="flex shrink-0 items-center gap-1 font-normal hover:text-foreground">
          {size !== "small" && <span className="text-[10px]">Powered by VoiceGateway</span>}
          <img src={mark} alt="" className="size-3" />
        </a>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2 px-3.5">{children}</div>
      {note && <p className="truncate px-3.5 text-[10px] text-muted-foreground">{note}</p>}
    </Card>
  )
}

const Big = ({ children, unit, alert }: { children: React.ReactNode; unit?: string; alert?: boolean }) => (
  <p className={cn("text-[34px] leading-none font-semibold tracking-[-0.03em] tnum", alert && "text-destructive")}>
    {children}
    {unit && <span className="ml-1 text-[14px] font-medium tracking-normal text-muted-foreground">{unit}</span>}
  </p>
)
const Sub = ({ children }: { children: React.ReactNode }) => <p className="text-[12px] leading-snug text-muted-foreground tnum">{children}</p>
const Waiting = ({ q }: { q: { isPending: boolean; error: unknown } }) => <p className="m-auto text-center text-[12px] text-muted-foreground">{stateOf(q)}</p>

// Answer time: the median against the budget, and how many turns ran over it.
function AnswerTime({ size }: { size: Size }) {
  const q = useStats()
  const a = q.data?.answers
  const inside = a && a.turns !== null && a.overBudget !== null && a.turns > 0 ? Math.round(((a.turns - a.overBudget) / a.turns) * 100) : null
  const over = a ? a.p50Ms !== null && a.p50Ms > a.budgetMs : false
  return (
    <Tile title={size === "small" ? "Answer time" : "Answer time · 7 days"} size={size} note={size === "medium" ? "Caller stops talking to the agent's first audio" : undefined}>
      {!a ? (
        <Waiting q={q} />
      ) : size === "small" ? (
        <>
          <Big unit={a.p50Ms === null ? undefined : "ms"} alert={over}>
            {a.p50Ms === null ? "Unknown" : Math.round(a.p50Ms).toLocaleString("en")}
          </Big>
          <Sub>Median, last 7 days</Sub>
          <CategoryBar className="mt-auto" values={[a.budgetMs * 0.67, a.budgetMs * 0.33, a.budgetMs]} colors={["emerald", "amber", "alert"]} marker={a.p50Ms === null ? undefined : { value: Math.min(a.p50Ms, a.budgetMs * 2) }} showLabels={false} />
          <Sub>{`Budget ${a.budgetMs / 1000} s · ${inside === null ? "unknown" : `${inside}%`} in`}</Sub>
        </>
      ) : (
        <div className="flex min-h-0 flex-1 items-center gap-3">
          <div className="w-32 shrink-0">
            <AnswerGauge p50={a.p50Ms} budget={a.budgetMs} />
          </div>
          <div className="min-w-0 space-y-1.5">
            <Big unit={a.p50Ms === null ? undefined : "ms median"} alert={over}>
              {a.p50Ms === null ? "Unknown" : Math.round(a.p50Ms).toLocaleString("en")}
            </Big>
            <Sub>{`${a.p90Ms === null ? "Unknown" : fmtMs(a.p90Ms)} p90 · ${count(a.turns)} turns measured`}</Sub>
            <Sub>{`${inside === null ? "Unknown share" : `${inside}%`} of turns inside the ${a.budgetMs / 1000} s budget`}</Sub>
          </div>
        </div>
      )}
    </Tile>
  )
}

const dayConfig = { callCount: { label: "Calls", color: "var(--primary)" } } satisfies ChartConfig

// Calls: how many in the range, phone against web, and per day.
function Calls({ size }: { size: Size }) {
  const q = useStats()
  const s = q.data
  const days = useMemo(() => (s?.byDay ?? []).slice(-7).map((d) => ({ ...d, day: new Date(`${d.date}T00:00:00Z`).toLocaleDateString("en", { weekday: "short", timeZone: "UTC" }) })), [s])
  return (
    <Tile title={size === "small" ? "Calls" : `Calls per day · ${count(s?.callCount)} in 7 days`} size={size}>
      {!s ? (
        <Waiting q={q} />
      ) : size === "small" ? (
        <>
          <Big>{count(s.callCount)}</Big>
          <Sub>Last 7 days</Sub>
          <div className="mt-auto space-y-0.5">
            <Sub>{`${count(s.byChannel.phone)} phone · ${count(s.byChannel.web)} web`}</Sub>
            <Sub>{`Today ${count(s.byDay.at(-1)?.callCount)}`}</Sub>
          </div>
        </>
      ) : (
        <ChartContainer config={dayConfig} className="min-h-0 w-full flex-1">
          <BarChart data={days} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
            <XAxis dataKey="day" tickLine={false} axisLine={false} tickMargin={4} fontSize={10} />
            <ChartTooltip cursor={false} content={<ChartTooltipContent hideIndicator />} />
            <Bar dataKey="callCount" fill="var(--color-callCount)" radius={4} isAnimationActive={false} />
          </BarChart>
        </ChartContainer>
      )}
    </Tile>
  )
}

// Issues: event issues with a hit in the range, counted by severity, and the top ones, all from listIssues.
// getCallStats.issueCounts stays null until the Issues engine computes issues server side.
function Issues({ size }: { size: Size }) {
  const { from, to } = useRange()
  const list = useQuery({ ...issuesInQuery(from, to), refetchInterval: 60_000 })
  const items = useMemo(() => (list.data ? issuesFrom(list.data).filter((i) => i.severity !== "info") : []), [list.data])
  const c = useMemo(() => {
    if (!list.data) return undefined
    const n = { high: 0, medium: 0, low: 0 }
    for (const i of items) if (i.kind === "event" && i.severity !== "info") n[i.severity]++
    return n
  }, [list.data, items])
  const total = c ? c.high + c.medium + c.low : null
  const shown = size === "large" ? 5 : 3
  const counts = c && (
    <div className="flex flex-wrap gap-x-2.5 gap-y-0.5 text-[12px] text-muted-foreground tnum">
      {(["high", "medium", "low"] as const).map((k) => (
        <span key={k} className="flex items-center gap-1 whitespace-nowrap">
          <span className={cn("size-2 rounded-full", sevDot[k])} aria-hidden />
          {`${count(c[k])} ${k}`}
        </span>
      ))}
    </div>
  )
  return (
    <Tile title={size === "small" ? "Issues" : "Issues · 7 days"} size={size} note={size === "medium" && items.length > shown ? `${items.length - shown} more` : undefined}>
      {size === "small" ? (
        !c ? (
          <Waiting q={list} />
        ) : (
          <>
            <Big alert={!!c.high}>{count(total)}</Big>
            <Sub>In the last 7 days</Sub>
            <div className="mt-auto">{counts}</div>
          </>
        )
      ) : (
        <>
          {size === "large" && c && (
            <div className="flex min-w-0 items-center gap-3">
              <Big alert={!!c.high}>{count(total)}</Big>
              {counts}
            </div>
          )}
          {!list.data ? (
            <Waiting q={list} />
          ) : !items.length ? (
            <p className="m-auto text-[13px] text-muted-foreground">No issues in the last 7 days.</p>
          ) : (
            <ItemGroup className="gap-0">
              {items.slice(0, shown).map((i) => (
                <Item key={i.id} role="listitem" size="sm" className="px-0 py-1.5">
                  <ItemMedia>
                    <span role="img" className={cn("size-2 rounded-full", sevDot[i.severity])} aria-label={`${i.severity} severity`} />
                  </ItemMedia>
                  <ItemContent className="min-w-0">
                    <ItemTitle className="block w-full truncate text-[12.5px] font-normal">{i.title}</ItemTitle>
                  </ItemContent>
                  <ItemActions className="text-[11.5px] text-muted-foreground tnum">{i.kind === "standing" ? "Current" : `${i.hits.length} ${i.hits.length === 1 ? "place" : "places"}`}</ItemActions>
                </Item>
              ))}
            </ItemGroup>
          )}
        </>
      )}
    </Tile>
  )
}

const ago = new Intl.RelativeTimeFormat("en", { numeric: "auto", style: "short" })
function since(iso: string) {
  const s = (Date.parse(iso) - Date.now()) / 1000
  for (const [unit, n] of [["day", 86400], ["hour", 3600], ["minute", 60]] as const) if (Math.abs(s) >= n) return ago.format(Math.round(s / n), unit)
  return "just now"
}
const length = (s: number | null) => {
  if (s === null) return "unknown"
  const t = Math.round(s)
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`
}
const channel: Record<CallSummary["channel"], string> = { phone: "Phone", web: "Web", console: "Console", local: "Local", unknown: "Channel unknown" }
const worst = (c: CallSummary) => (c.issues.high ? "bg-destructive" : c.issues.medium ? "bg-amber-500" : c.issues.high === null || c.issues.medium === null ? "bg-muted-foreground/30" : "bg-emerald-500")

// Recent calls: the latest calls, newest first; a row opens the one-call view in the same frame.
function RecentCalls({ size, open }: { size: Size; open: (id: string) => void }) {
  const n = size === "large" ? 7 : 3
  const q = useQuery({ queryKey: ["embed-recent", n], queryFn: () => fetchCallPage(new URLSearchParams({ sort: "startedAt", order: "desc", archived: "false" }), undefined, n), refetchInterval: 30_000 })
  return (
    <Tile title="Recent calls" size={size} note={size === "medium" ? "Length · median answer" : "Dot: the worst issue on the call"}>
      {!q.data ? (
        <Waiting q={q} />
      ) : !q.data.items.length ? (
        <p className="m-auto text-[13px] text-muted-foreground">No calls yet.</p>
      ) : (
        <ItemGroup className="gap-0">
          {q.data.items.map((c) => (
            <div key={c.id} role="listitem">
              <Item size="sm" asChild className="px-0 py-1.5">
                <button type="button" onClick={() => open(c.id)} className="w-full text-left">
                  <ItemMedia>
                    <span className={cn("size-2 rounded-full", worst(c))} aria-hidden />
                  </ItemMedia>
                  <ItemContent className="min-w-0">
                    <ItemTitle className="block w-full truncate text-[12.5px] font-normal">{`${channel[c.channel]} · ${since(c.startedAt)}${size === "large" && c.turnCount !== null ? ` · ${c.turnCount} turns` : ""}`}</ItemTitle>
                  </ItemContent>
                  <ItemActions className="text-[11.5px] text-muted-foreground tnum">{`${length(c.durationSeconds)} · ${c.answerMsMedian === null ? "unknown" : fmtMs(c.answerMsMedian)}`}</ItemActions>
                </button>
              </Item>
            </div>
          ))}
        </ItemGroup>
      )}
    </Tile>
  )
}

// One stage chain as a bar: each reported stage's median, in order. A stage nothing reported is
// left out, never drawn as zero.
function Chain({ chain }: { chain: CallStats["chain"] }) {
  const parts = (chain ?? []).filter((c) => c.medianMs !== null)
  if (!parts.length) return <Sub>No stage timings reported.</Sub>
  return (
    <div className="space-y-1.5">
      <CategoryBar values={parts.map((p) => p.medianMs ?? 0)} colors={parts.map((_, i) => stageColors[i % stageColors.length])} showLabels={false} />
      <div className="flex flex-wrap gap-x-2.5 gap-y-0.5 text-[10.5px] text-muted-foreground tnum">
        {parts.map((p, i) => (
          <span key={p.stage} className="flex items-center gap-1">
            <span className={cn("size-1.5 rounded-sm", getColorClassName(stageColors[i % stageColors.length], "bg"))} aria-hidden />
            {`${p.stage} ${fmtMs(p.medianMs ?? 0)}`}
          </span>
        ))}
      </div>
    </div>
  )
}

// Time per turn: where a turn's time goes, for all agents, and on the large tile per agent.
function TimePerTurn({ size }: { size: Size }) {
  const q = useStats()
  const s = q.data
  const agents = useMemo(() => (s?.agents ?? []).filter((a) => a.chain?.length).sort((x, y) => (y.callCount ?? 0) - (x.callCount ?? 0)).slice(0, 3), [s])
  return (
    <Tile title={size === "medium" ? "Time per turn · median" : "Time per turn by agent · median"} size={size} note={s ? `Over ${count(s.anatomyTurns)} turns in 7 days` : undefined}>
      {!s ? (
        <Waiting q={q} />
      ) : size === "medium" ? (
        <>
          <Big unit={s.answers.p50Ms === null ? undefined : "ms"}>{s.answers.p50Ms === null ? "Unknown" : Math.round(s.answers.p50Ms).toLocaleString("en")}</Big>
          <Chain chain={s.chain} />
        </>
      ) : !agents.length ? (
        <p className="m-auto text-[13px] text-muted-foreground">No stage timings reported.</p>
      ) : (
        <div className="space-y-3">
          {agents.map((a) => (
            <div key={a.key ?? "none"} className="space-y-1">
              <div className="flex justify-between gap-2 text-[12px]">
                <span className="truncate">{a.name ?? (a.key === null ? "Agent not identified" : "Unnamed agent")}</span>
                <span className="shrink-0 text-muted-foreground tnum">{a.answerP50Ms === null ? "unknown" : fmtMs(a.answerP50Ms)}</span>
              </div>
              <Chain chain={a.chain} />
            </div>
          ))}
        </div>
      )}
    </Tile>
  )
}

function OneCall({ callId, back }: { callId: string | undefined; back: () => void }) {
  const read = useQuery({ ...callQuery(callId ?? ""), enabled: callId !== undefined })
  if (!callId) return <p className="p-6 text-center text-[13px] text-muted-foreground">Pass &amp;call=&lt;call id&gt; to show a call.</p>
  return (
    <div className="flex h-svh flex-col bg-background p-3">
      <button type="button" onClick={back} className="mb-2 flex w-fit items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground">
        <ArrowLeft size={13} /> Recent calls
      </button>
      {!sharesContent() && <p className="mb-2 text-[12px] text-muted-foreground">Timings only: this embed does not share transcripts or audio.</p>}
      <div className="min-h-0 flex-1 overflow-hidden rounded-xl border">
        {read.data ? (
          <CallView call={read.data} />
        ) : read.error instanceof ApiError && read.error.status === 404 ? (
          <p className="p-6 text-center text-[13px] text-muted-foreground">{`No call ${callId} in this project.`}</p>
        ) : (
          <Waiting q={read} />
        )}
      </div>
    </div>
  )
}

export function EmbedPage() {
  const { widget } = useParams({ strict: false }) as { widget?: string }
  const search = useSearch({ strict: false }) as { call?: string; theme?: string; size?: string; token?: string }
  const navigate = useNavigate()
  const meta = widgets.find((w) => w.id === widget)
  // The size the frame asked for, when the widget comes in it; otherwise its first size.
  const size: Size = meta ? ((meta.sizes as readonly string[]).includes(search.size ?? "") ? (search.size as Size) : meta.sizes[0]) : "medium"
  // Links inside keep the token, theme and size, so a call opened from Recent calls reads and looks the same.
  const go = (to: WidgetId, call?: string) => navigate({ to: "/embed/$widget", params: { widget: to }, search: { token: search.token, theme: search.theme, size: search.size, call } } as never)

  if (!meta)
    return (
      <div className="flex h-svh flex-col items-center justify-center gap-1 bg-background p-4 text-center text-[13px] text-muted-foreground">
        <p>{`No widget named ${widget ?? ""}.`}</p>
        <p>{`Widgets: ${widgets.map((w) => w.id).join(", ")}.`}</p>
      </div>
    )
  return (
    <div className="h-svh bg-background text-foreground">
      {meta.id === "answer-time" && <AnswerTime size={size} />}
      {meta.id === "calls" && <Calls size={size} />}
      {meta.id === "issues" && <Issues size={size} />}
      {meta.id === "recent-calls" && <RecentCalls size={size} open={(id) => go("call", id)} />}
      {meta.id === "time-per-turn" && <TimePerTurn size={size} />}
      {meta.id === "call" && <OneCall callId={search.call} back={() => go("recent-calls")} />}
    </div>
  )
}
