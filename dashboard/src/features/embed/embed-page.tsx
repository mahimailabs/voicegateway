import { useMemo } from "react"
import { useNavigate, useParams, useSearch } from "@tanstack/react-router"
import { ArrowLeft } from "@phosphor-icons/react"
import mark from "@/assets/brand/mark.svg"
import { DataTable } from "@/components/data-table"
import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { ApiError, callQuery, callPagesQuery, issuesQuery } from "@/lib/api"
import { BUDGET_MS } from "@/lib/thresholds"
import { cn } from "@/lib/utils"
import { CallView } from "@/features/call/call-view"
import { columns, rowOf, serverSort, type Row } from "@/features/calls/calls-page"
import type { SortingState } from "@tanstack/react-table"
import { useUrlState } from "@/lib/url-state"
import { issuesFrom, type Severity } from "@/features/issues/issues-model"
import { AnswerTimeFigure, useCallStats } from "@/features/overview/overview"
import { TurnAnatomy, lanesFrom } from "@/features/overview/visuals"
import type { CallStats } from "@/lib/api-types"
import { widgets } from "./widgets"

// One widget in a frame on someone else's site (decision 0023): no sidebar, no top bar, no links into
// the console. Links inside open other widgets. Until the Phase 2 server mints embed tokens, every
// widget reads the captured data and says it is a preview.

const sevDot: Record<Severity, string> = { high: "bg-destructive", medium: "bg-amber-500", low: "bg-muted-foreground/50", info: "bg-muted-foreground/30" }
const noTags = () => [] as string[]

function AnswerTime() {
  const stats = useCallStats()
  if (!stats.data) return <Note>{stats.isError ? "Answer times could not be loaded." : "Loading answer times."}</Note>
  return (
    <div className="flex flex-col items-center justify-center pt-2">
      <AnswerTimeFigure answers={(stats.data as CallStats).answers} />
    </div>
  )
}

function Anatomy() {
  const stats = useCallStats()
  if (!stats.data) return <Note>{stats.isError ? "Turn anatomy could not be loaded." : "Loading turn anatomy."}</Note>
  const s = stats.data as CallStats
  const realtime = s.agents.filter((a) => a.stages.some((st) => st.stage === "Realtime")).map((a) => a.name ?? (a.key === null ? "Agent not identified" : "Unnamed agent"))
  return <TurnAnatomy lanes={lanesFrom(s.stages, realtime)} e2e={s.answers.p50Ms} turns={s.anatomyTurns} budget={BUDGET_MS} />
}

const Note = ({ children }: { children: React.ReactNode }) => <p className="py-10 text-center text-[13px] text-muted-foreground">{children}</p>

function Calls({ open }: { open: (callId: string) => void }) {
  // The table's sort is the server's, read a page at a time; newest first by default.
  const [sorting] = useUrlState<SortingState>("sort", [{ id: "started", desc: true }])
  const key = sorting[0] && serverSort[sorting[0].id] ? sorting[0] : { id: "started", desc: true }
  const list = useInfiniteQuery(callPagesQuery(new URLSearchParams({ archived: "false", sort: serverSort[key.id], order: key.desc ? "desc" : "asc" })))
  const rows = useMemo(() => (list.data?.pages ?? []).flatMap((p) => p.items).map((s) => rowOf(s)), [list.data])
  const cols = useMemo(() => columns(noTags), [])
  if (list.isPending) return <Note>Loading calls.</Note>
  if (list.isError) return <Note>Calls could not be loaded.</Note>
  return (
    <DataTable<Row>
      data={rows}
      columns={cols}
      filtered
      server={{ total: list.data?.pages[0]?.totals.callCount ?? null, hasMore: Boolean(list.hasNextPage), loading: list.isFetchingNextPage, loadMore: () => void list.fetchNextPage() }}
      getRowId={(r) => r.summary.roomSid ?? r.summary.id}
      onRowClick={(r) => open(r.summary.id)}
      empty="No calls in this project yet."
    />
  )
}

function OneCall({ callId, back }: { callId: string | undefined; back: () => void }) {
  const read = useQuery({ ...callQuery(callId ?? ""), enabled: callId !== undefined })
  const call = read.data
  if (!callId) return <Note>Pass ?call=&lt;call id&gt; to show a call.</Note>
  if (read.error instanceof ApiError && read.error.status === 404) return <Note>{`No call ${callId} in this project.`}</Note>
  if (read.isError) return <Note>The call could not be loaded.</Note>
  if (!call) return <Note>Loading the call.</Note>
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
  const read = useQuery(issuesQuery())
  const list = useMemo(() => (read.data ? issuesFrom(read.data) : []), [read.data])
  if (read.isPending) return <Note>Loading issues.</Note>
  if (read.isError) return <Note>Issues could not be loaded.</Note>
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
  const search = useSearch({ strict: false }) as { call?: string; theme?: string }
  const navigate = useNavigate()
  const meta = widgets.find((w) => w.id === widget)
  const go = (to: string, call?: string) => navigate({ to: "/embed/$widget", params: { widget: to }, search: { theme: search.theme, call } } as never)

  return (
    <div className="flex min-h-svh flex-col bg-background text-foreground">
      <main className="flex-1 p-4">
        {widget === "answer-time" && <AnswerTime />}
        {widget === "turn-anatomy" && <Anatomy />}
        {widget === "calls" && <Calls open={(id) => go("call", id)} />}
        {widget === "call" && <OneCall callId={search.call} back={() => go("calls")} />}
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
