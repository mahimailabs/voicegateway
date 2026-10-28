import { CaretRight, CircleHalf, Clock, DownloadSimple, Flask, Hash, Info, Pulse, ShieldWarning, Stack, WarningCircle } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { CategoryBar } from "@/components/tremor/CategoryBar"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { type Columns, DataTable, type Field } from "@/components/data-table"
import { DataGridColumnHeader } from "@/components/reui/data-grid/data-grid-column-header"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { useQuery } from "@tanstack/react-query"
import { QueryState } from "@/components/page-state"
import { ingressQuery } from "@/lib/api"
import type { Ingress as IngressData } from "@/lib/api-types"
import { useUrlState } from "@/lib/url-state"
import { fmtDateTime, fmtSeconds as secs } from "@/lib/format"

// Media pushed or pulled into the project's rooms, as ListIngress returns it. An endpoint stays listed
// until deleted, but its state covers only the current or last session; earlier sessions need LiveKit's
// ingress webhooks (Phase 2). The stream key is a credential and is never read into the dashboard.

type Ingress = IngressData["ingress"][number]
// Either input track: audio has channels and sample rate, video has size and frame rate.
type Media = (Partial<NonNullable<Ingress["audioIn"]>> & Partial<NonNullable<Ingress["videoIn"]>>) | null
// RFC 3339 to Unix seconds, keeping an unreported time unknown.
const sec = (t: string | null) => (t === null ? null : Date.parse(t) / 1000)
// Every endpoint here came from our own test; the project has none of its own.
const TEST = true

const docs = "https://docs.livekit.io"
const typeName: Record<string, string> = { RTMP_INPUT: "RTMP", WHIP_INPUT: "WHIP", URL_INPUT: "URL pull" }
const statusName = (s: string) => s.replace("ENDPOINT_", "").toLowerCase()
const statusTone = (s: string) =>
  s === "ENDPOINT_PUBLISHING"
    ? "bg-primary/10 text-primary"
    : s === "ENDPOINT_ERROR"
      ? "bg-destructive/10 text-destructive"
      : s === "ENDPOINT_BUFFERING"
        ? "bg-amber-500/10 text-amber-700 dark:text-amber-400"
        : "bg-muted text-muted-foreground"
const clock = (s: number) => fmtDateTime(s * 1000)
const codec = (m?: string | null) => (m ? m.split("/")[1].toUpperCase() : null)
const media = (m: Media | null) =>
  m?.mimeType
    ? [codec(m.mimeType), m.channels && `${m.channels === 2 ? "stereo" : m.channels === 1 ? "mono" : `${m.channels} ch`}`, m.sampleRate && `${m.sampleRate / 1000} kHz`, m.width && `${m.width}x${m.height}`].filter(Boolean).join(", ")
    : null
const session = (e: Ingress) => {
  const start = sec(e.startedAt)
  const end = sec(e.endedAt)
  return start !== null && end !== null ? end - start : null
}

const right = { headerClassName: "[&>div]:w-full [&>div]:justify-end", cellClassName: "text-right" }
const ingressColumns: Columns<Ingress> = [
  {
    id: "name",
    accessorFn: (e) => e.name,
    header: ({ column }) => <DataGridColumnHeader title="Ingress" column={column} />,
    cell: ({ row }) => (
      <span className="flex items-start gap-2">
        <CaretRight size={12} className={cn("mt-1 text-muted-foreground transition-transform", row.getIsExpanded() && "rotate-90")} />
        <span>
          <span className="text-[13px]">{row.original.name}</span>
          <span className="block font-mono text-[11px] text-muted-foreground">{row.original.id}</span>
        </span>
      </span>
    ),
    meta: {
      expandedContent: (e: Ingress) => (
        <div className="bg-muted/30 p-4">
          <Detail e={e} />
        </div>
      ),
    },
  },
  { id: "type", accessorFn: (e) => typeName[e.inputType] ?? e.inputType, header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />, cell: ({ getValue }) => <span className="text-[13px]">{String(getValue())}</span> },
  { id: "room", accessorFn: (e) => e.room, header: ({ column }) => <DataGridColumnHeader title="Room" column={column} />, cell: ({ row }) => <span className="font-mono text-[12px] text-muted-foreground">{row.original.room}</span> },
  {
    id: "status",
    accessorFn: (e) => e.status,
    header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
    cell: ({ row }) => <span className={cn("rounded-full px-2 py-0.5 text-[12px]", statusTone(row.original.status))}>{statusName(row.original.status)}</span>,
  },
  {
    id: "started",
    accessorFn: (e) => sec(e.startedAt) ?? -1,
    header: ({ column }) => <DataGridColumnHeader title="Last session" column={column} />,
    cell: ({ row }) => <span className="text-[13px] tnum">{row.original.startedAt ? clock(sec(row.original.startedAt)!) : "none"}</span>,
  },
  {
    id: "length",
    accessorFn: (e) => session(e) ?? -1,
    header: ({ column }) => <DataGridColumnHeader title="Length" column={column} />,
    meta: right,
    cell: ({ row }) => <span className="text-[13px] tnum">{session(row.original) !== null ? secs(session(row.original)!) : "unknown"}</span>,
  },
  {
    id: "media",
    header: "Media in",
    enableSorting: false,
    cell: ({ row: { original: e } }) => (
      <span className="text-[12px]">
        {media(e.audioIn) ?? <span className="text-muted-foreground">none received</span>}
        {e.audioIn?.averageBitrate ? <span className="block text-[11px] text-muted-foreground tnum">{Math.round(e.audioIn.averageBitrate / 1000)} kbps average</span> : null}
      </span>
    ),
  },
]
const ingressFields: Field<Ingress>[] = [
  {
    id: "status",
    label: "Status",
    type: "select",
    icon: <CircleHalf />,
    options: ["ENDPOINT_INACTIVE", "ENDPOINT_BUFFERING", "ENDPOINT_PUBLISHING", "ENDPOINT_ERROR", "ENDPOINT_COMPLETE"].map((v) => ({ value: v, label: statusName(v) })),
    get: (e) => e.status,
  },
  { id: "type", label: "Type", type: "select", icon: <Stack />, options: Object.entries(typeName).map(([value, label]) => ({ value, label })), get: (e) => e.inputType },
  { id: "room", label: "Room", type: "text", icon: <Hash />, get: (e) => e.room },
  { id: "length", label: "Session length (s)", type: "number", icon: <Clock />, get: (e) => session(e) },
]

type Check = { severity: "high" | "medium" | "low"; title: string; where: string; why: string; fix: string; doc: string }
function checks(list: Ingress[]): Check[] {
  const out: Check[] = []
  for (const e of list.filter((x) => x.status === "ENDPOINT_ERROR"))
    out.push({
      severity: "high",
      title: `${typeName[e.inputType] ?? e.inputType} ingress failed: ${e.error ?? "no reason given"}`,
      where: e.id,
      why: `The ingress could not ${e.inputType === "URL_INPUT" ? "pull its source" : "take the stream"}${session(e) !== null ? ` and stopped after ${secs(session(e)!)}` : ""}. Nothing was published to ${e.room}.${e.inputType === "URL_INPUT" ? " A URL ingress starts as soon as it is created and cannot be reused." : ""}`,
      fix: e.inputType === "URL_INPUT" ? "Check the source URL is reachable from LiveKit Cloud over HTTP(S) or SRT, then create a new ingress." : "Check the encoder settings against the ingress URL and stream key.",
      doc: `${docs}/reference/other/ingress/api/`,
    })
  const open = list.filter((e) => e.reusable && e.enabled && e.status !== "ENDPOINT_PUBLISHING")
  if (open.length)
    out.push({
      severity: "low",
      title: `${open.length} idle ${open.length === 1 ? "endpoint accepts" : "endpoints accept"} new streams`,
      where: open.map((e) => e.id).join(", "),
      why: "A reusable endpoint stays live between sessions. Anyone holding its stream key can publish into the room until it is disabled or deleted.",
      fix: "Set enabled to false when the endpoint is not in use, or delete it.",
      doc: `${docs}/reference/other/ingress/api/`,
    })
  if (list.length)
    out.push({
      severity: "low",
      title: "Only the last session is kept",
      where: "ListIngress",
      why: "ListIngress reports each endpoint's current or last session. Earlier sessions, and how many there were, need the ingress_started and ingress_ended webhooks.",
      fix: "Point LiveKit's webhooks at the VoiceGateway server (Phase 2).",
      doc: `${docs}/reference/other/ingress/api/`,
    })
  return out
}
const tone = { high: "bg-destructive", medium: "bg-amber-500", low: "bg-muted-foreground/40" }

function StatCard({ icon: I, value, label, children, alert }: { icon: Icon; value: React.ReactNode; label: string; children?: React.ReactNode; alert?: boolean }) {
  return (
    <div className="rounded-2xl border bg-background p-4">
      <div className="flex items-center justify-between">
        <p className="text-[13px] text-muted-foreground">{label}</p>
        <I size={18} className={alert ? "text-destructive" : "text-muted-foreground"} />
      </div>
      <p className={cn("mt-2 text-[28px] leading-none font-semibold tracking-[-0.02em] tnum", alert && "text-destructive")}>{value}</p>
      {children && <div className="mt-3">{children}</div>}
    </div>
  )
}

function Detail({ e }: { e: Ingress }) {
  const t0 = sec(e.history[0]?.at ?? null)
  const out = e.published.map((t) => codec(t.mimeType)).filter(Boolean)
  return (
    <div className="grid gap-6 sm:grid-cols-2">
      <div className="space-y-3 text-[13px]">
        {e.error && <p className="rounded-lg bg-destructive/10 px-3 py-2 font-mono text-[12px] break-words text-destructive">{e.error}</p>}
        <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-1.5">
          <dt className="text-muted-foreground">{e.inputType === "URL_INPUT" ? "Source" : "Ingest URL"}</dt>
          <dd className="font-mono text-[12px] break-all">{e.url}</dd>
          <dt className="text-muted-foreground">Publishes as</dt>
          <dd>
            <span className="font-mono text-[12px]">{e.participant}</span> in <span className="font-mono text-[12px]">{e.room}</span>
          </dd>
          <dt className="text-muted-foreground">Media in</dt>
          <dd>
            {media(e.audioIn) ?? "none received"}
            {e.audioIn?.averageBitrate ? `, ${Math.round(e.audioIn.averageBitrate / 1000)} kbps average` : ""}
          </dd>
          <dt className="text-muted-foreground">Into the room</dt>
          <dd>{out.length ? `${out.join(", ")}${e.transcoding ? ", transcoded" : e.transcoding === null ? ", transcoding unknown" : ""}` : "nothing published"}</dd>
          <dt className="text-muted-foreground">Endpoint</dt>
          <dd>{[
            e.reusable === null ? "reuse unknown" : e.reusable ? "reusable" : "single use",
            e.enabled === null ? "enabled unknown" : e.enabled ? "enabled" : "disabled",
            e.transcoding === null ? "transcoding unknown" : e.transcoding ? "transcoding on" : "transcoding off",
          ].join(", ")}</dd>
        </dl>
        {e.audioIn?.averageBitrate && <p className="text-[12px] text-muted-foreground">The average bitrate appeared only after the session ended; while publishing, ListIngress reported none.</p>}
      </div>
      <div className="space-y-2">
        <p className="text-[12px] text-muted-foreground">Status changes, read every 2 s</p>
        <ol className="space-y-1.5 text-[13px]">
          {e.history.map((h, k) => {
            const t = sec(h.at)
            return (
              <li key={k} className="flex items-center gap-2">
                <span className={cn("rounded-full px-2 py-0.5 text-[11px]", statusTone(h.status))}>{statusName(h.status)}</span>
                <span className="text-muted-foreground tnum">{t === null ? "time unknown" : t0 !== null ? `+${(t - t0).toFixed(0)} s` : clock(t)}</span>
              </li>
            )
          })}
        </ol>
      </div>
    </div>
  )
}

export function IngressPage() {
  const [tab, setTab] = useUrlState("tab", "endpoints")
  const read = useQuery(ingressQuery())
  if (!read.data) return <QueryState icon={DownloadSimple} what="ingress" query={read} />
  const d = read.data
  const list = d.ingress
  const list2 = checks(list)
  const by = (s: string) => list.filter((e) => e.status === s).length
  const types = [...new Set(list.map((e) => typeName[e.inputType] ?? e.inputType))]
  const last = list.filter((e) => session(e) !== null && e.status !== "ENDPOINT_ERROR")
  const sev = (s: Check["severity"]) => list2.filter((c) => c.severity === s).length

  return (
    <div className="mx-auto max-w-[1200px] space-y-12">
      {TEST && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-[13px] text-amber-800 dark:text-amber-300">
          <Flask size={16} /> Test data. Two endpoints we created on the project for a test and deleted after; the project has no ingress of its own.
        </div>
      )}
      <header className="space-y-6">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.02em]">Ingress</h1>
          {types.map((t) => (
            <span key={t} className="rounded-full bg-muted px-2.5 py-0.5 text-[13px] text-muted-foreground">
              {t}
            </span>
          ))}
          <Tooltip>
            <TooltipTrigger asChild>
              <button type="button" aria-label="How this was read" className="text-muted-foreground hover:text-foreground">
                <Info size={18} />
              </button>
            </TooltipTrigger>
            <TooltipContent className="max-w-96 text-xs leading-relaxed">{d.source}</TooltipContent>
          </Tooltip>
        </div>
        <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
          <StatCard icon={DownloadSimple} value={list.length} label="Endpoints">
            <CategoryBar values={[by("ENDPOINT_PUBLISHING"), by("ENDPOINT_BUFFERING"), by("ENDPOINT_INACTIVE"), by("ENDPOINT_ERROR")]} colors={["accent", "amber", "quiet", "alert"]} showLabels={false} />
            <p className="mt-1.5 text-[11px] text-muted-foreground">{`${by("ENDPOINT_PUBLISHING")} publishing · ${by("ENDPOINT_INACTIVE")} idle · ${by("ENDPOINT_ERROR")} error`}</p>
          </StatCard>
          <StatCard icon={Pulse} value={last.length ? secs(last.reduce((a, e) => a + session(e)!, 0)) : "Unknown"} label="Last sessions">
            <p className="text-[11px] text-muted-foreground">{last.length ? `${last.length} ${last.length === 1 ? "session" : "sessions"} that published; earlier ones need webhooks` : "No session seen"}</p>
          </StatCard>
          <StatCard icon={WarningCircle} value={by("ENDPOINT_ERROR")} label="In error" alert={by("ENDPOINT_ERROR") > 0}>
            <p className="truncate text-[11px] text-muted-foreground">{list.find((e) => e.error)?.error ?? "None"}</p>
          </StatCard>
          <StatCard icon={ShieldWarning} value={list2.length} label="Checks to fix" alert={list2.some((c) => c.severity === "high")}>
            <CategoryBar values={[sev("high"), sev("medium"), sev("low")]} colors={["alert", "amber", "quiet"]} showLabels={false} />
            <p className="mt-1.5 text-[11px] text-muted-foreground">{`${sev("high")} high · ${sev("medium")} medium · ${sev("low")} low`}</p>
          </StatCard>
        </div>
      </header>

      <Tabs value={tab} onValueChange={setTab} className="gap-5">
        <TabsList>
          <TabsTrigger value="endpoints">
            Endpoints <span className="ml-1 text-[11px] text-muted-foreground tnum">{list.length}</span>
          </TabsTrigger>
          <TabsTrigger value="checks">
            Checks <span className="ml-1 rounded-full bg-destructive/10 px-1.5 text-[11px] text-destructive tnum">{list2.length}</span>
          </TabsTrigger>
        </TabsList>
        <TabsContent value="endpoints">
          {list.length === 0 ? (
            <p className="rounded-2xl border border-dashed px-4 py-8 text-center text-[13px] text-muted-foreground">No ingress endpoints in this project.</p>
          ) : (
            <DataTable data={list} columns={ingressColumns} fields={ingressFields} getRowId={(e) => e.id} canExpand={() => true} empty="No ingress matches these filters." />
          )}
        </TabsContent>
        <TabsContent value="checks">
          <ul className="divide-y rounded-xl border">
            {list2.map((c, k) => (
              <li key={k}>
                <Collapsible>
                  <CollapsibleTrigger className="group flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/50">
                    <span className={cn("size-2 shrink-0 rounded-full", tone[c.severity])} />
                    <span className="flex-1 text-[14px]">{c.title}</span>
                    <span className="max-w-[40%] truncate font-mono text-[11px] text-muted-foreground">{c.where}</span>
                    <CaretRight size={14} className="text-muted-foreground transition-transform group-data-[state=open]:rotate-90" />
                  </CollapsibleTrigger>
                  <CollapsibleContent className="space-y-1.5 px-4 pb-3 pl-9 text-[13px]">
                    <p className="text-muted-foreground">{c.why}</p>
                    <p>
                      <span className="font-medium">Fix: </span>
                      {c.fix}{" "}
                      <a href={c.doc} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                        Docs
                      </a>
                    </p>
                  </CollapsibleContent>
                </Collapsible>
              </li>
            ))}
          </ul>
        </TabsContent>
      </Tabs>
    </div>
  )
}
