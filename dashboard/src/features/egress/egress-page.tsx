import { CaretRight, CircleHalf, Clock, CloudArrowUp, Hash, Info, Prohibit, ShieldWarning, Stack, Waveform } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { AudioPlayerButton, AudioPlayerProgress, AudioPlayerProvider, AudioPlayerTime } from "@/components/elevenlabs/audio-player"
import { CategoryBar } from "@/components/tremor/CategoryBar"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { type Columns, DataTable, type Field } from "@/components/data-table"
import { DataGridColumnHeader } from "@/components/reui/data-grid/data-grid-column-header"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { useQuery } from "@tanstack/react-query"
import { QueryState } from "@/components/page-state"
import { egressQuery } from "@/lib/api"
import type { Egress as EgressData } from "@/lib/api-types"
import { useUrlState } from "@/lib/url-state"

// Recordings and streams out of the project's rooms, as ListEgress returns them. ListEgress keeps only
// recent egress, so history needs LiveKit's egress webhooks (Phase 2). The request is kept as its shape
// only: where it writes and in what format, never the storage credentials.

type Egress = EgressData["egress"][number]
// RFC 3339 to Unix seconds, keeping an unreported time unknown.
const sec = (t: string | null) => (t === null ? null : Date.parse(t) / 1000)

const docs = "https://docs.livekit.io"
const outcome = (s: string) => s.replace("EGRESS_", "").replace("_", " ").toLowerCase()
const statusTone = (s: string) =>
  s === "EGRESS_COMPLETE"
    ? "bg-primary/10 text-primary"
    : s === "EGRESS_FAILED"
      ? "bg-destructive/10 text-destructive"
      : s === "EGRESS_ABORTED" || s === "EGRESS_LIMIT_REACHED"
        ? "bg-amber-500/10 text-amber-700 dark:text-amber-400"
        : "bg-muted text-muted-foreground"
const storageName: Record<string, string> = { s3: "S3", gcp: "Google Cloud Storage", azure: "Azure Blob", aliOSS: "Alibaba OSS" }
const kindName: Record<string, string> = { roomComposite: "Room recording", web: "Web page", participant: "Participant", trackComposite: "Track composite", track: "Track" }
const clock = (s: number) => new Date(s * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
const secs = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s` : `${s.toFixed(1)} s`)
const size = (b: number) => (b >= 1024 ** 2 ? `${(b / 1024 ** 2).toFixed(1)} MB` : `${Math.round(b / 1024)} KB`)
// A file that recorded audio but reached no bucket: the length is known, the bytes are gone. An
// unreported size or length is not evidence of a loss.
const lost = (e: Egress) => e.status === "EGRESS_FAILED" && e.file !== null && e.file.sizeBytes === 0 && e.file.durationS !== null && e.file.durationS > 0
const kept = (e: Egress) => e.status === "EGRESS_COMPLETE" && e.file !== null && e.file.sizeBytes !== null && e.file.sizeBytes > 0

const length = (e: Egress) => {
  if (e.file?.durationS) return e.file.durationS
  const start = sec(e.startedAt)
  const end = sec(e.endedAt)
  return start !== null && end !== null ? end - start : null
}
// The known seconds of a set of files, and whether any length was unreported.
const audio = (list: Egress[]) => ({ s: list.reduce((a, e) => a + (e.file?.durationS ?? 0), 0), partial: list.some((e) => e.file?.durationS === null) })
const right = { headerClassName: "[&>div]:w-full [&>div]:justify-end", cellClassName: "text-right" }
const egressColumns: Columns<Egress> = [
  {
    id: "id",
    accessorFn: (e) => e.id,
    size: 190,
    header: ({ column }) => <DataGridColumnHeader title="Egress" column={column} />,
    cell: ({ row }) => (
      <span className="flex items-center gap-2 font-mono text-[12px]">
        <CaretRight size={12} className={cn("text-muted-foreground transition-transform", row.getIsExpanded() && "rotate-90")} />
        {row.original.id}
      </span>
    ),
    meta: {
      expandedContent: (e: Egress) => (
        <div className="bg-muted/30 p-4">
          <Detail e={e} />
        </div>
      ),
    },
  },
  {
    id: "room",
    size: 230,
    accessorFn: (e) => e.room ?? "",
    header: ({ column }) => <DataGridColumnHeader title="Room" column={column} />,
    cell: ({ row }) => <span className="font-mono text-[12px] text-muted-foreground">{row.original.room ?? "unknown"}</span>,
  },
  {
    id: "type",
    accessorFn: (e) => e.request?.kind ?? "",
    header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
    cell: ({ row: { original: e } }) => (
      <span className="text-[12px]">
        {e.request ? `${kindName[e.request.kind ?? ""] ?? e.request.kind}${e.request.audioOnly ? ", audio" : ""}` : "unknown"}
        <span className="block text-[11px] text-muted-foreground">
          {e.request?.fileType ?? ""} to {e.request?.storage ? (storageName[e.request.storage] ?? e.request.storage) : "unknown storage"}
        </span>
      </span>
    ),
  },
  {
    id: "status",
    accessorFn: (e) => e.status,
    header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
    cell: ({ row }) => <span className={cn("rounded-full px-2 py-0.5 text-[12px]", statusTone(row.original.status))}>{outcome(row.original.status)}</span>,
  },
  {
    id: "started",
    accessorFn: (e) => sec(e.startedAt) ?? -1,
    header: ({ column }) => <DataGridColumnHeader title="Started" column={column} />,
    cell: ({ row }) => <span className="text-[13px] tnum">{row.original.startedAt ? clock(sec(row.original.startedAt)!) : "unknown"}</span>,
  },
  {
    id: "length",
    accessorFn: (e) => length(e) ?? -1,
    header: ({ column }) => <DataGridColumnHeader title="Length" column={column} />,
    meta: right,
    cell: ({ row }) => <span className="text-[13px] tnum">{length(row.original) !== null ? secs(length(row.original)!) : "unknown"}</span>,
  },
  {
    id: "file",
    accessorFn: (e) => e.file?.sizeBytes ?? -1,
    header: ({ column }) => <DataGridColumnHeader title="File" column={column} />,
    meta: right,
    cell: ({ row: { original: e } }) => <span className={cn("text-[13px] tnum", lost(e) && "text-destructive")}>{!e.file ? "none" : lost(e) ? "lost" : e.file.sizeBytes === null ? "unknown" : size(e.file.sizeBytes)}</span>,
  },
]
const egressFields: Field<Egress>[] = [
  {
    id: "status",
    label: "Status",
    type: "select",
    icon: <CircleHalf />,
    options: ["EGRESS_COMPLETE", "EGRESS_FAILED", "EGRESS_ABORTED", "EGRESS_ACTIVE", "EGRESS_STARTING", "EGRESS_ENDING", "EGRESS_LIMIT_REACHED"].map((v) => ({ value: v, label: outcome(v) })),
    get: (e) => e.status,
  },
  { id: "type", label: "Type", type: "select", icon: <Stack />, options: Object.entries(kindName).map(([value, label]) => ({ value, label })), get: (e) => e.request?.kind ?? null },
  { id: "room", label: "Room", type: "text", icon: <Hash />, get: (e) => e.room },
  { id: "length", label: "Length (s)", type: "number", icon: <Clock />, get: (e) => length(e) },
  { id: "lost", label: "Recording lost", type: "boolean", icon: <Waveform />, get: (e) => lost(e) },
]

type Check = { severity: "high" | "medium" | "low"; title: string; where: string; why: string; fix: string; doc: string }
function checks(list: Egress[], rejected: EgressData["rejected"]): Check[] {
  const out: Check[] = []
  const failed = list.filter(lost)
  if (failed.length) {
    const sig = failed.some((e) => e.error?.includes("SignatureDoesNotMatch"))
    out.push({
      severity: "high",
      title: `${failed.length} ${failed.length === 1 ? "recording" : "recordings"} lost on upload`,
      where: failed.map((e) => e.id).join(", "),
      why: `LiveKit recorded ${secs(audio(failed).s)} of audio, then the upload to the bucket failed${sig ? " with 403 SignatureDoesNotMatch" : ""}, so no file was stored.${sig ? " In our test this was Google Cloud Storage reached through its S3 endpoint with an HMAC key." : ""}`,
      fix: sig ? "Use the gcp upload with a service account key for Google Cloud Storage; check the key, bucket and region for S3." : "Check the bucket name, credentials and region in the storage config.",
      doc: `${docs}/transport/media/ingress-egress/egress/outputs/`,
    })
  }
  const aborted = list.filter((e) => e.status === "EGRESS_ABORTED")
  if (aborted.length)
    out.push({
      severity: "medium",
      title: `${aborted.length} ${aborted.length === 1 ? "egress" : "egresses"} aborted before recording`,
      where: aborted.map((e) => e.id).join(", "),
      why: `LiveKit reported "${aborted[0].error}". The room had nobody publishing while the egress waited, so nothing was recorded and no file was written.`,
      fix: "Start the egress once a participant is publishing, or start it from the agent after it joins.",
      doc: `${docs}/reference/other/egress/api/`,
    })
  if (rejected.length)
    out.push({
      severity: "low",
      title: "Egress request rejected: no storage",
      where: "StartEgress",
      why: "LiveKit Cloud has no local storage, so a request that names no bucket fails with 400 at start and never appears in ListEgress. We saw this only because we sent it.",
      fix: "Set storage on the output, on the request, or as the project's default.",
      doc: `${docs}/transport/media/ingress-egress/egress/outputs/`,
    })
  if (list.length)
    out.push({
      severity: "low",
      title: "Egress history is not kept",
      where: "ListEgress",
      why: "ListEgress returns recent egress only. Older jobs, and why a job ended, need the egress_started and egress_ended webhooks.",
      fix: "Point LiveKit's webhooks at the VoiceGateway server (Phase 2).",
      doc: `${docs}/deploy/admin/quotas-and-limits/`,
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

function Detail({ e }: { e: Egress }) {
  const t0 = sec(e.history[0]?.at ?? null)
  return (
    <div className="grid gap-6 sm:grid-cols-2">
      <div className="space-y-3 text-[13px]">
        {e.error && <p className="rounded-lg bg-destructive/10 px-3 py-2 font-mono text-[12px] break-words text-destructive">{e.error}</p>}
        {e.details && !e.error && <p className="text-muted-foreground">{e.details}</p>}
        <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-1.5">
          <dt className="text-muted-foreground">Request</dt>
          <dd>{e.request ? [kindName[e.request.kind ?? ""] ?? e.request.kind, e.request.audioOnly && "audio only", e.request.fileType].filter(Boolean).join(", ") : "unknown"}</dd>
          <dt className="text-muted-foreground">Storage</dt>
          <dd>{e.request?.storage ? (storageName[e.request.storage] ?? e.request.storage) : "unknown"}</dd>
          <dt className="text-muted-foreground">File</dt>
          <dd className="font-mono text-[12px] break-all">{e.file ? e.file.name : "none written"}</dd>
          {e.file?.bucketHost && (
            <>
              <dt className="text-muted-foreground">Bucket</dt>
              <dd className="font-mono text-[12px] break-all">
                {e.file.bucketHost} <span className="font-sans text-muted-foreground">(test bucket, deleted)</span>
              </dd>
            </>
          )}
        </dl>
        {e.file?.playable && (
          <AudioPlayerProvider>
            <div className="flex items-center gap-3 rounded-xl border px-3 py-2">
              <AudioPlayerButton item={{ id: e.id, src: e.file.playable }} variant="default" size="icon-sm" className="rounded-full" />
              <AudioPlayerProgress className="flex-1" />
              <span className="text-[12px] text-muted-foreground tnum">
                <AudioPlayerTime /> / {e.file.durationS === null ? "unknown" : secs(e.file.durationS)}
              </span>
            </div>
          </AudioPlayerProvider>
        )}
      </div>
      <div className="space-y-2">
        <p className="text-[12px] text-muted-foreground">{e.sampled ? "Status changes, read every 2 s" : "Final status only, read once after it ended"}</p>
        <ol className="space-y-1.5 text-[13px]">
          {e.history.map((h) => {
            const t = sec(h.at)
            return (
              <li key={h.status} className="flex items-center gap-2">
                <span className={cn("rounded-full px-2 py-0.5 text-[11px]", statusTone(h.status))}>{outcome(h.status)}</span>
                <span className="text-muted-foreground tnum">{t === null ? "time unknown" : e.sampled && t0 !== null ? `+${(t - t0).toFixed(0)} s` : clock(t)}</span>
              </li>
            )
          })}
        </ol>
      </div>
    </div>
  )
}

export function EgressPage() {
  const [tab, setTab] = useUrlState("tab", "jobs")
  const read = useQuery(egressQuery())
  if (!read.data) return <QueryState icon={CloudArrowUp} what="egress" query={read} />
  const d = read.data
  const list = d.egress
  const list2 = checks(list, d.rejected)
  const by = (s: string) => list.filter((e) => e.status === s).length
  const stored = audio(list.filter(kept))
  const gone = audio(list.filter(lost))
  // A length nobody reported is not counted as zero: the figure says it is a floor.
  const atLeast = stored.partial || gone.partial ? "at least " : ""
  const keptS = stored.s
  const lostS = gone.s
  const sev = (s: Check["severity"]) => list2.filter((c) => c.severity === s).length

  return (
    <div className="mx-auto max-w-[1200px] space-y-12">
      <header className="space-y-6">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.02em]">Egress</h1>
          <span className="rounded-full bg-muted px-2.5 py-0.5 text-[13px] text-muted-foreground">recent only</span>
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
          <StatCard icon={CloudArrowUp} value={list.length} label="Egress jobs">
            <CategoryBar values={[by("EGRESS_COMPLETE"), by("EGRESS_FAILED"), by("EGRESS_ABORTED")]} colors={["accent", "alert", "amber"]} showLabels={false} />
            <p className="mt-1.5 text-[11px] text-muted-foreground">{`${by("EGRESS_COMPLETE")} complete · ${by("EGRESS_FAILED")} failed · ${by("EGRESS_ABORTED")} aborted`}</p>
          </StatCard>
          <StatCard icon={Waveform} value={secs(keptS + lostS)} label="Audio recorded" alert={lostS > 0}>
            <CategoryBar values={[keptS, lostS]} colors={["accent", "alert"]} showLabels={false} />
            <p className="mt-1.5 text-[11px] text-muted-foreground">{`${atLeast}${secs(keptS)} stored · ${secs(lostS)} lost on upload`}</p>
          </StatCard>
          <StatCard icon={Prohibit} value={d.rejected.length} label="Rejected at start">
            <p className="text-[11px] text-muted-foreground">{d.rejected.length ? "Not in ListEgress; seen because we sent it" : "None seen"}</p>
          </StatCard>
          <StatCard icon={ShieldWarning} value={list2.length} label="Checks to fix" alert={list2.some((c) => c.severity === "high")}>
            <CategoryBar values={[sev("high"), sev("medium"), sev("low")]} colors={["alert", "amber", "quiet"]} showLabels={false} />
            <p className="mt-1.5 text-[11px] text-muted-foreground">{`${sev("high")} high · ${sev("medium")} medium · ${sev("low")} low`}</p>
          </StatCard>
        </div>
      </header>

      <Tabs value={tab} onValueChange={setTab} className="gap-5">
        <TabsList>
          <TabsTrigger value="jobs">
            Jobs <span className="ml-1 text-[11px] text-muted-foreground tnum">{list.length}</span>
          </TabsTrigger>
          <TabsTrigger value="checks">
            Checks <span className="ml-1 rounded-full bg-destructive/10 px-1.5 text-[11px] text-destructive tnum">{list2.length}</span>
          </TabsTrigger>
        </TabsList>
        <TabsContent value="jobs">
          {list.length === 0 ? (
            <p className="rounded-2xl border border-dashed px-4 py-8 text-center text-[13px] text-muted-foreground">No recent egress. ListEgress keeps recent jobs only; older ones need LiveKit's egress webhooks (Phase 2).</p>
          ) : (
            <DataTable data={list} columns={egressColumns} fields={egressFields} getRowId={(e) => e.id} canExpand={() => true} sort={[{ id: "started", desc: true }]} empty="No egress matches these filters." />
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
