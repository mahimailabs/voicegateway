import { Link } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { CheckCircle, Clock, Plug, WarningCircle } from "@phosphor-icons/react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item"
import { CopyCommand } from "@/components/copy-command"
import { QueryState } from "@/components/page-state"
import { connectionQuery } from "@/lib/api"
import type { ConnectionSource, ProjectConnection } from "@/lib/api-types"
import { REQUEST_CAPACITY } from "@/lib/support"
import { type Source, type StepState, fixText, sourceState } from "./connection-model"
import { cn } from "@/lib/utils"

// First-call setup: the two URLs to copy, then three steps that move from Waiting to Connected as
// ingest accepts the project's first webhook, its first agent upload and stores its first call. A
// refused request shows why and how to fix it. Every state comes from getProjectConnection; the
// console infers nothing.

const fmt = (iso: string) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })

function Status({ state }: { state: StepState["state"] }) {
  if (state === "connected")
    return (
      <Badge variant="secondary" className="font-normal">
        <CheckCircle data-icon="inline-start" />
        Connected
      </Badge>
    )
  if (state === "error")
    return (
      <Badge variant="destructive" className="font-normal">
        <WarningCircle data-icon="inline-start" />
        Error
      </Badge>
    )
  return (
    <Badge variant="outline" className="font-normal text-muted-foreground">
      <Clock data-icon="inline-start" />
      Waiting
    </Badge>
  )
}

function Step({ title, state, children, extra }: { title: string; state: StepState["state"]; children: React.ReactNode; extra?: React.ReactNode }) {
  return (
    <Item role="listitem" variant="outline" className="items-start" aria-label={title}>
      <ItemContent className="min-w-0">
        <ItemTitle>{title}</ItemTitle>
        <ItemDescription className="line-clamp-none">{children}</ItemDescription>
        {extra}
      </ItemContent>
      <ItemActions>
        <Status state={state} />
      </ItemActions>
    </Item>
  )
}

// A LiveKit API key in code text that wraps anywhere, so a 64-character key never widens a phone.
const KeyText = ({ k }: { k: string }) => <code className="rounded bg-muted px-1 font-mono text-[12px] break-all text-foreground">{k}</code>

// Why a request was refused and its fix, as inline text (it sits inside a paragraph).
function Refused({ source, s, at, heading = true }: { source: Source; s: ConnectionSource; at: string; heading?: boolean }) {
  const { title, fix, keys, capacity } = fixText(source, s)
  return (
    <>
      {heading && <span className="block font-medium text-foreground">{title}</span>}
      {keys && (
        <span className="block">
          {keys.used === null ? "The token named an unreadable key" : <>Used key <KeyText k={keys.used} /></>}
          {", this project expects "}
          {keys.expected === null ? "a key that is not known" : <KeyText k={keys.expected} />}.
        </span>
      )}
      <span className="block">{fix}</span>
      {capacity && (
        <Button variant="link" size="sm" className="h-auto px-0" asChild>
          <a href={REQUEST_CAPACITY.href} target="_blank" rel="noreferrer">
            {REQUEST_CAPACITY.label}
          </a>
        </Button>
      )}
      <span className="block">{`Refused ${fmt(at)}.`}</span>
    </>
  )
}

function SourceStep({ title, source, s, now, waiting }: { title: string; source: Source; s: ConnectionSource; now: number; waiting: string }) {
  const st = sourceState(s, now)
  if (st.state === "waiting") return <Step title={title} state="waiting">{waiting}</Step>
  if (st.state === "error")
    return (
      <Step title={title} state="error">
        <Refused source={source} s={s} at={st.at} />
      </Step>
    )
  // Accepted recently, then refused: still Connected, with the refused request as a warning.
  const warning = st.refused && (
    <Alert role="status" className="mt-1">
      <WarningCircle />
      <AlertTitle>{`Last request refused: ${fixText(source, s).title}`}</AlertTitle>
      <AlertDescription>
        <p>
          <Refused source={source} s={s} at={st.refused.at} heading={false} />
        </p>
      </AlertDescription>
    </Alert>
  )
  return (
    <Step title={title} state="connected" extra={warning}>
      {`Last accepted ${fmt(st.at)}.`}
    </Step>
  )
}

function UrlField({ label, hint, url }: { label: string; hint: string; url: string | null }) {
  return (
    <div className="min-w-0 space-y-1.5">
      <p className="text-[13px] font-medium">{label}</p>
      <p className="text-[13px] text-muted-foreground">{hint}</p>
      {url === null ? <p className="text-[13px] text-muted-foreground">Not available yet.</p> : <CopyCommand text={url} label={label} copyLabel={`Copy ${label.toLowerCase()}`} />}
    </div>
  )
}

export function ConnectAgentView({ connection: c, now, framed = true }: { connection: ProjectConnection; now: number; framed?: boolean }) {
  const noPublicUrl = c.urls.observability === null || c.urls.webhook === null
  return (
    <section aria-labelledby="connect-agent-title" className={cn("space-y-5", framed && "rounded-2xl border bg-background p-5")}>
      <div className="space-y-1">
        <h2 id="connect-agent-title" className="text-[15px] font-semibold">
          Connect your agent
        </h2>
        <p className="text-[13px] text-muted-foreground">Copy both URLs, then make a call. Each step turns Connected as VoiceGateway receives it.</p>
      </div>
      {noPublicUrl && (
        <Alert role="status">
          <WarningCircle />
          <AlertTitle>VOICEGATEWAY_PUBLIC_URL is not set on this instance</AlertTitle>
          <AlertDescription>
            <p>Without it these URLs cannot be built. On a self-hosted install, set VOICEGATEWAY_PUBLIC_URL in deploy/.env to the address LiveKit and your agent reach VoiceGateway on, then run make up.</p>
          </AlertDescription>
        </Alert>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <UrlField label="Agent upload URL" hint="Set LIVEKIT_OBSERVABILITY_URL in your agent to this value." url={c.urls.observability} />
        <UrlField label="Webhook URL" hint="Add this URL as a webhook in LiveKit." url={c.urls.webhook} />
      </div>
      <ItemGroup className="gap-2">
        <SourceStep title="Webhook" source="webhook" s={c.webhook} now={now} waiting="No webhook from LiveKit yet." />
        <SourceStep title="Agent upload" source="agent" s={c.agent} now={now} waiting="No upload from your agent yet." />
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
  // States are judged as of the read they come from, so a render never changes them on its own.
  return <ConnectAgentView connection={read.data} now={read.dataUpdatedAt} framed={framed} />
}
