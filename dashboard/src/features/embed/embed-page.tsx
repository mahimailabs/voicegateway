import { useMemo } from "react"
import { useNavigate, useParams, useSearch } from "@tanstack/react-router"
import { ArrowLeft } from "@phosphor-icons/react"
import mark from "@/assets/brand/mark.svg"
import { DataTable } from "@/components/data-table"
import { useQuery } from "@tanstack/react-query"
import { callQuery, callsQuery } from "@/lib/api"
import { BUDGET_MS } from "@/lib/thresholds"
import { cn } from "@/lib/utils"
import { CallView } from "@/features/call/call-view"
import { columns, rowOf, type Row } from "@/features/calls/calls-page"
import { issues, type Severity } from "@/features/issues/issues-model"
import { fmtS, useOverview } from "@/features/overview/overview"
import { AnswerGauge, TurnAnatomy, anatomy } from "@/features/overview/visuals"
import { widgets } from "./widgets"

// One widget in a frame on someone else's site (decision 0023): no sidebar, no top bar, no links into
// the console. Links inside open other widgets. Until the Phase 2 server mints embed tokens, every
// widget reads the captured data and says it is a preview.

const sevDot: Record<Severity, string> = { high: "bg-destructive", medium: "bg-amber-500", low: "bg-muted-foreground/50", info: "bg-muted-foreground/30" }
const noTags = () => [] as string[]

function AnswerTime() {
  const o = useOverview()
  const over = o.answers.filter((a) => a > BUDGET_MS).length
  return (
    <div className="flex flex-col items-center justify-center pt-2">
      <AnswerGauge p50={o.p50} budget={BUDGET_MS} />
      <p className={cn("mt-2 text-[34px] leading-none font-semibold tracking-[-0.03em] tnum", o.p50 !== null && o.p50 > BUDGET_MS && "text-destructive")}>{o.p50 === null ? "Unknown" : fmtS(o.p50)}</p>
      <p className="mt-1.5 text-[12px] text-muted-foreground tnum">{`median answer · ${over} of ${o.answers.length} turns over ${BUDGET_MS / 1000} s`}</p>
    </div>
  )
}

function Anatomy() {
  const o = useOverview()
  const shape = useMemo(() => anatomy(o.fleetRows.filter((r) => r.calls > 0).map((r) => ({ name: r.name, calls: r.callList }))), [o])
  return <TurnAnatomy lanes={shape.lanes} e2e={shape.e2e} turns={shape.turns} budget={BUDGET_MS} />
}

const Note = ({ children }: { children: React.ReactNode }) => <p className="py-10 text-center text-[13px] text-muted-foreground">{children}</p>

function Calls({ open }: { open: (room: string) => void }) {
  const list = useQuery(callsQuery())
  const rows = useMemo(() => (list.data?.items ?? []).map((s) => rowOf(s)).sort((a, b) => b.started - a.started), [list.data])
  const cols = useMemo(() => columns(noTags), [])
  if (list.isPending) return <Note>Loading calls.</Note>
  if (list.isError) return <Note>Calls could not be loaded.</Note>
  return <DataTable<Row> data={rows} columns={cols} getRowId={(r) => r.summary.roomSid ?? r.summary.id} onRowClick={(r) => open(r.summary.room)} pageSize={10} empty="No calls in this project yet." />
}

function OneCall({ room, back }: { room: string | undefined; back: () => void }) {
  const list = useQuery(callsQuery())
  const summary = room ? list.data?.items.find((s) => s.room === room) : undefined
  const read = useQuery({ ...callQuery(summary?.id ?? ""), enabled: summary !== undefined })
  const call = read.data
  if (!room) return <Note>Pass ?room=&lt;room name&gt; to show a call.</Note>
  if (list.isError || read.isError) return <Note>The call could not be loaded.</Note>
  if (list.isSuccess && !summary) return <Note>{`No call named ${room} in this project.`}</Note>
  if (!call) return <Note>{`Loading ${room}.`}</Note>
  return (
    <div className="flex h-[calc(100svh-6.5rem)] min-h-[520px] flex-col">
      <button type="button" onClick={back} className="mb-2 flex w-fit items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground">
        <ArrowLeft size={13} /> All calls
      </button>
      <div className="min-h-0 flex-1 overflow-hidden rounded-xl border">
        <CallView call={call} />
      </div>
    </div>
  )
}

function Issues() {
  const list = useMemo(() => issues(), [])
  if (!list.length) return <p className="py-10 text-center text-[13px] text-muted-foreground">No issues found.</p>
  return (
    <ul className="divide-y rounded-xl border">
      {list.map((i) => (
        <li key={i.id} className="flex items-center gap-3 px-4 py-3">
          <span className={cn("size-2 shrink-0 rounded-full", sevDot[i.severity])} aria-label={`${i.severity} severity`} />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] font-medium">{i.title}</span>
            <span className="block text-[12px] text-muted-foreground">{i.source}</span>
          </span>
          <span className="shrink-0 text-[12px] text-muted-foreground tnum">{`${i.hits.length} ${i.hits.length === 1 ? "place" : "places"}`}</span>
        </li>
      ))}
    </ul>
  )
}

export function EmbedPage() {
  const { widget } = useParams({ strict: false }) as { widget?: string }
  const search = useSearch({ strict: false }) as { room?: string; theme?: string }
  const navigate = useNavigate()
  const meta = widgets.find((w) => w.id === widget)
  const go = (to: string, room?: string) => navigate({ to: "/embed/$widget", params: { widget: to }, search: { theme: search.theme, room } } as never)

  return (
    <div className="flex min-h-svh flex-col bg-background text-foreground">
      <main className="flex-1 p-4">
        {widget === "answer-time" && <AnswerTime />}
        {widget === "turn-anatomy" && <Anatomy />}
        {widget === "calls" && <Calls open={(room) => go("call", room)} />}
        {widget === "call" && <OneCall room={search.room} back={() => go("calls")} />}
        {widget === "issues" && <Issues />}
        {!meta && (
          <div className="py-10 text-center text-[13px] text-muted-foreground">
            <p>{`No widget named ${widget ?? ""}.`}</p>
            <p className="mt-1">{`Widgets: ${widgets.map((w) => w.id).join(", ")}.`}</p>
          </div>
        )}
      </main>
      <footer className="flex flex-wrap items-center justify-between gap-2 border-t px-4 py-2 text-[11px] text-muted-foreground">
        <span>Preview from captured data. Embed tokens arrive with the server.</span>
        <a href="https://voicegateway.dev" target="_blank" rel="noopener noreferrer" className="flex items-center gap-1.5 hover:text-foreground">
          <img src={mark} alt="" className="size-3.5" />
          Powered by VoiceGateway
        </a>
      </footer>
    </div>
  )
}
