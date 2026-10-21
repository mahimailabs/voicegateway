import { Link } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { Plug } from "@phosphor-icons/react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item"
import { CopyCommand } from "@/components/copy-command"
import { QueryState } from "@/components/page-state"
import { connectionQuery } from "@/lib/api"
import type { ConnectionSource, ProjectConnection } from "@/lib/api-types"
import { type Source, type StepState, fixText, sourceState } from "./connection-model"
import { cn } from "@/lib/utils"

// First-call setup: the two URLs to copy, then three steps that move from Waiting to Connected as
// ingest accepts the project's first webhook, its first agent upload and stores its first call. A
// refused request shows why and how to fix it. Every state comes from getProjectConnection; the
// console infers nothing.

const fmt = (iso: string) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })

function Status({ state }: { state: StepState["state"] }) {
  const label = state === "connected" ? "Connected" : state === "error" ? "Error" : "Waiting"
  return (
    <Badge variant="outline" className="gap-1.5 font-normal">
      <span aria-hidden className={cn("size-2 rounded-full", state === "connected" ? "bg-good" : state === "error" ? "bg-destructive" : "bg-muted-foreground/40")} />
      {label}
    </Badge>
  )
}

function Step({ title, state, children }: { title: string; state: StepState["state"]; children: React.ReactNode }) {
  return (
    <Item role="listitem" variant="outline" className="items-start" aria-label={title}>
      <ItemContent className="min-w-0">
        <ItemTitle>{title}</ItemTitle>
        <ItemDescription className="line-clamp-none">{children}</ItemDescription>
      </ItemContent>
      <ItemActions>
        <Status state={state} />
      </ItemActions>
    </Item>
  )
}

function SourceStep({ title, source, s, waiting }: { title: string; source: Source; s: ConnectionSource; waiting: string }) {
  const st = sourceState(s)
  if (st.state === "connected") return <Step title={title} state="connected">{`Last accepted ${fmt(st.at)}.`}</Step>
  if (st.state === "waiting") return <Step title={title} state="waiting">{waiting}</Step>
  const { title: what, fix } = fixText(source, s)
  return (
    <Step title={title} state="error">
      <span className="block font-medium text-foreground">{what}</span>
      <span className="block">{fix}</span>
      <span className="block">{`Refused ${fmt(st.at)}.`}</span>
    </Step>
  )
}

function UrlField({ label, hint, url }: { label: string; hint: string; url: string | null }) {
  return (
    <div className="space-y-1.5">
      <p className="text-[13px] font-medium">{label}</p>
      <p className="text-[13px] text-muted-foreground">{hint}</p>
      {url === null ? (
        <p role="status" className="rounded-lg border px-3 py-2 text-[13px] text-muted-foreground">
          VOICEGATEWAY_PUBLIC_URL is not set on this instance, so this URL cannot be built. Set it to the address LiveKit and your agent reach VoiceGateway on, then restart web.
        </p>
      ) : (
        <CopyCommand text={url} label={label} copyLabel={`Copy ${label.toLowerCase()}`} />
      )}
    </div>
  )
}

export function ConnectAgentView({ connection: c, framed = true }: { connection: ProjectConnection; framed?: boolean }) {
  return (
    <section aria-labelledby="connect-agent-title" className={cn("space-y-5", framed && "rounded-2xl border bg-background p-5")}>
      <div className="space-y-1">
        <h2 id="connect-agent-title" className="text-[15px] font-semibold">
          Connect your agent
        </h2>
        <p className="text-[13px] text-muted-foreground">Copy both URLs, then make a call. Each step turns Connected as VoiceGateway receives it.</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <UrlField label="Observability URL" hint="Set LIVEKIT_OBSERVABILITY_URL in your agent to this value." url={c.urls.observability} />
        <UrlField label="Webhook URL" hint="Add this URL as a webhook in LiveKit." url={c.urls.webhook} />
      </div>
      <ItemGroup className="gap-2">
        <SourceStep title="Webhook" source="webhook" s={c.webhook} waiting="No webhook from LiveKit yet." />
        <SourceStep title="Agent upload" source="agent" s={c.agent} waiting="No upload from your agent yet." />
        {c.firstCall ? (
          <Step title="First call stored" state="connected">
            <span className="block">{c.firstCall.startedAt === null ? "Start time not reported." : `Started ${fmt(c.firstCall.startedAt)}.`}</span>
            <Button variant="link" size="sm" className="h-auto px-0" asChild>
              <Link to="/calls/$callId" params={{ callId: c.firstCall.id }}>
                Open the first call
              </Link>
            </Button>
          </Step>
        ) : (
          <Step title="First call stored" state="waiting">
            No call stored yet. Make a call to your agent.
          </Step>
        )}
      </ItemGroup>
    </section>
  )
}

export function ConnectAgentPanel({ framed = true }: { framed?: boolean }) {
  const read = useQuery(connectionQuery())
  if (!read.data) return <QueryState icon={Plug} what="the connection" query={read} />
  return <ConnectAgentView connection={read.data} framed={framed} />
}
