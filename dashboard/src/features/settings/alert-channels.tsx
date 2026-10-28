import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { EnvelopeSimple, Key, LinkSimple, PaperPlaneTilt, Trash, WebhooksLogo } from "@phosphor-icons/react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Item, ItemContent, ItemDescription, ItemGroup, ItemSeparator, ItemTitle } from "@/components/ui/item"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { ProviderLogo } from "@/components/provider-logo"
import { CopyCommand } from "@/components/copy-command"
import { errorText } from "@/components/page-state"
import { fmtHourMinute } from "@/lib/format"
import { alertSetupQuery, removeAlertChannel, saveAlertChannel, testAlertChannel } from "@/lib/api"
import type { AlertChannel, AlertChannelKind, AlertChannelUpdate, AlertDelivery } from "@/lib/api-types"

// Where this project's alerts go (alerts-behaviour.md, Delivery; alert-channels.md). An Admin sets
// each channel here and the server seals its URL, key or signing secret, so the browser sees only
// the host it posts to, and a webhook's secret once, in the answer that made it.

const ago = (iso: string) => {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000)
  if (s < 90) return "just now"
  if (s < 5400) return `${Math.round(s / 60)} min ago`
  if (s < 129600) return `${Math.round(s / 3600)} h ago`
  return new Date(iso).toLocaleDateString()
}

// The badge for a configured channel: broken first, then its latest delivery.
function deliveryBadge(c: AlertChannel | undefined): { text: string; tone: "ok" | "warn" | "bad" | "none" } {
  if (!c) return { text: "Not set up", tone: "none" }
  if (c.broken) return { text: "Broken", tone: "bad" }
  const d = c.lastDelivery
  if (!d) return { text: "Nothing sent yet", tone: "none" }
  if (d.state === "delivered") return { text: `Delivered ${d.deliveredAt ? ago(d.deliveredAt) : ""}`.trim(), tone: "ok" }
  if (d.state === "failed") return { text: "Not delivered", tone: "bad" }
  if (d.state === "retrying") return { text: "Retrying", tone: "warn" }
  if (d.state === "cancelled") return { text: "Nothing sent yet", tone: "none" }
  return { text: "Sending", tone: "warn" }
}
const toneClass = { ok: "text-emerald-700 dark:text-emerald-400", warn: "text-amber-700 dark:text-amber-400", bad: "border-destructive/40 text-destructive", none: "text-muted-foreground" }

// The latest delivery in one line: what it carried, its answer and the provider's message.
function lastDeliveryText(d: AlertDelivery) {
  const what = d.change === "test" ? "Test" : d.change[0].toUpperCase() + d.change.slice(1)
  const parts = [`${what} ${d.state} ${ago(d.createdAt)}`, d.status, d.message].filter(Boolean)
  if (d.state === "retrying" && d.nextAttemptAt) parts.push(`next try ${fmtHourMinute(d.nextAttemptAt)}`)
  return parts.join(" · ")
}

// The chat channels take the incoming webhook URL their app hands out; the server checks the host.
const chats = [
  { kind: "slack", name: "Slack", description: "Post each alert to a Slack channel.", placeholder: "https://hooks.slack.com/services/...", how: "In Slack, add an app with Incoming Webhooks to the channel and paste its webhook URL." },
  { kind: "teams", name: "Microsoft Teams", description: "Post each alert to a Teams channel as an Adaptive Card.", placeholder: "https://....logic.azure.com/workflows/...", how: "In Teams, add the Workflows template \"Post to a channel when a webhook request is received\" and paste its URL." },
  { kind: "discord", name: "Discord", description: "Post each alert to a Discord channel. Nobody is mentioned.", placeholder: "https://discord.com/api/webhooks/...", how: "In Discord, open the channel's settings, Integrations, Webhooks, and copy the webhook URL." },
] as const

// The paging channels take an integration key and the account's region.
const pagers = [
  { kind: "pagerduty", name: "PagerDuty", description: "Open a PagerDuty incident on firing and resolve it when the alert resolves.", label: "Integration key", how: "Add an Events API v2 integration to a PagerDuty service and paste its integration key. Send test records a change event, which pages nobody." },
  { kind: "opsgenie", name: "Opsgenie", description: "Open an Opsgenie alert on firing and close it when the alert resolves.", label: "API key", how: "Add an API integration in Opsgenie and paste its key. Send test creates a P5 alert you can close." },
] as const

const removeText: Record<AlertChannelKind, string> = {
  webhook: "The URL and signing secret are deleted and cannot be shown again. Adding the webhook again makes a new secret, so every receiver needs it.",
  email: "The recipients are deleted.",
  slack: "The webhook URL is deleted.",
  teams: "The webhook URL is deleted.",
  discord: "The webhook URL is deleted.",
  pagerduty: "The integration key is deleted.",
  opsgenie: "The API key is deleted.",
}

const logo = (provider: string) => <ProviderLogo provider={provider} size={20} />

export function AlertChannels({ isAdmin, selfHosted }: { isAdmin: boolean; selfHosted: boolean }) {
  const read = useQuery(alertSetupQuery())
  const channels = read.data?.channels ?? []
  const find = (k: AlertChannelKind) => channels.find((c) => c.kind === k)
  return (
    <ItemGroup className="gap-0 has-data-[size=sm]:gap-0">
      <ChannelItem kind="webhook" mark={<WebhooksLogo size={20} />} name="Webhook" description="POST each alert as JSON, signed with Standard Webhooks." channel={find("webhook")} isAdmin={isAdmin} loaded={read.isSuccess}>
        <WebhookForm configured={Boolean(find("webhook"))} />
      </ChannelItem>
      <ItemSeparator className="my-0" />
      <ChannelItem kind="email" mark={<EnvelopeSimple size={20} />} name="Email" description="Send each alert to a list of addresses." channel={find("email")} isAdmin={isAdmin} loaded={read.isSuccess}>
        {read.data?.emailAvailable ? (
          <EmailForm recipients={find("email")?.recipients ?? []} />
        ) : (
          <p className="text-[13px] text-muted-foreground">{selfHosted ? "This server sends no email. Set VOICEGATEWAY_SMTP_URL on the server, then add recipients here." : "Email is not available on this workspace yet."}</p>
        )}
      </ChannelItem>
      {chats.map((x) => (
        <span key={x.kind} className="contents">
          <ItemSeparator className="my-0" />
          <ChannelItem kind={x.kind} mark={logo(x.kind)} name={x.name} description={x.description} channel={find(x.kind)} isAdmin={isAdmin} loaded={read.isSuccess}>
            <UrlForm kind={x.kind} name={x.name} placeholder={x.placeholder} how={x.how} configured={Boolean(find(x.kind))} />
          </ChannelItem>
        </span>
      ))}
      {pagers.map((x) => (
        <span key={x.kind} className="contents">
          <ItemSeparator className="my-0" />
          <ChannelItem kind={x.kind} mark={logo(x.kind)} name={x.name} description={x.description} channel={find(x.kind)} isAdmin={isAdmin} loaded={read.isSuccess}>
            <KeyForm kind={x.kind} name={x.name} label={x.label} how={x.how} configured={Boolean(find(x.kind))} />
          </ChannelItem>
        </span>
      ))}
    </ItemGroup>
  )
}

function ChannelItem({ kind, mark, name, description, channel, isAdmin, loaded, children }: { kind: AlertChannelKind; mark: React.ReactNode; name: string; description: string; channel?: AlertChannel; isAdmin: boolean; loaded: boolean; children: React.ReactNode }) {
  const qc = useQueryClient()
  const refresh = () => qc.invalidateQueries({ queryKey: alertSetupQuery().queryKey })
  const test = useMutation({ mutationFn: () => testAlertChannel(kind), onSuccess: () => (toast.success(`Test alert queued for ${name}`), refresh()), onError: (e: Error) => toast.error(errorText(e)) })
  const remove = useMutation({ mutationFn: () => removeAlertChannel(kind), onSuccess: () => (toast.success(`${name} removed`), refresh()), onError: (e: Error) => toast.error(errorText(e)) })
  const [confirming, setConfirming] = useState(false)
  const badge = deliveryBadge(channel)
  return (
    <Item role="listitem" size="sm" className="flex-col items-stretch gap-3 rounded-none border-0 px-0 py-4">
      <div className="flex items-center gap-4">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg border bg-background">
          {mark}
        </span>
        <ItemContent className="min-w-0 gap-0.5">
          <ItemTitle className="text-[14px] font-medium">{name}</ItemTitle>
          <ItemDescription className={channel ? "truncate font-mono text-[12px]" : "line-clamp-none text-[13px]"}>{channel ? channel.target : description}</ItemDescription>
        </ItemContent>
        {loaded && (
          <Badge variant="outline" className={`shrink-0 font-normal ${toneClass[badge.tone]}`}>
            {badge.text}
          </Badge>
        )}
      </div>
      <div className="space-y-3 sm:pl-14">
        {channel?.broken && <p role="alert" className="text-[13px] text-destructive">The last delivery was refused with an answer retrying cannot fix. Check the address, then save it again.</p>}
        {channel?.lastDelivery && <p className="text-[12px] break-words text-muted-foreground">{lastDeliveryText(channel.lastDelivery)}</p>}
        {isAdmin ? children : !channel && <p className="text-[13px] text-muted-foreground">Only an Admin sets up a channel.</p>}
        {isAdmin && channel && (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={() => test.mutate()} disabled={test.isPending}>
              <PaperPlaneTilt /> Send test
            </Button>
            <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive" onClick={() => setConfirming(true)} disabled={remove.isPending}>
              <Trash /> Remove
            </Button>
          </div>
        )}
        <Dialog open={confirming} onOpenChange={setConfirming}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>{`Remove ${name}?`}</DialogTitle>
              <DialogDescription>{`${removeText[kind]} Rules stop sending here until it is set up again, and alerts still waiting to go here are cancelled.`}</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={() => {
                  remove.mutate()
                  setConfirming(false)
                }}
              >
                Remove
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </Item>
  )
}

function useSave(kind: AlertChannelKind, onSaved: (secret: string | null) => void) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: AlertChannelUpdate) => saveAlertChannel(kind, body),
    onSuccess: (saved) => {
      onSaved(saved.signingSecret)
      return qc.invalidateQueries({ queryKey: alertSetupQuery().queryKey })
    },
  })
}

// The URL is entered on every save: the saved one is sealed and never comes back.
function WebhookForm({ configured }: { configured: boolean }) {
  const [url, setUrl] = useState("")
  const [rotate, setRotate] = useState(false)
  const [secret, setSecret] = useState<string | null>(null)
  const save = useSave("webhook", (s) => {
    setSecret(s)
    setUrl("")
    setRotate(false)
  })
  const valid = /^https?:\/\/[^\s/]+/.test(url.trim()) && !save.isPending
  // The secret is shown this once: bring it into view and onto its Copy button, on a phone too.
  const shown = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!secret) return
    shown.current?.scrollIntoView?.({ block: "center" })
    shown.current?.querySelector("button")?.focus({ preventScroll: true })
  }, [secret])
  return (
    <form
      className="space-y-3"
      aria-label="Webhook"
      onSubmit={(e) => {
        e.preventDefault()
        if (valid) save.mutate(rotate ? { url: url.trim(), rotateSecret: true } : { url: url.trim() })
      }}
    >
      <Field>
        <FieldLabel htmlFor="alert-webhook-url">{configured ? "Replace the URL" : "Endpoint URL"}</FieldLabel>
        <InputGroup>
          <InputGroupAddon>
            <LinkSimple />
          </InputGroupAddon>
          <InputGroupInput id="alert-webhook-url" type="url" inputMode="url" autoComplete="off" placeholder="https://example.com/hooks/voicegateway" value={url} onChange={(e) => setUrl(e.target.value)} />
        </InputGroup>
        <FieldDescription>{configured ? "The saved URL is never shown again. Enter it, or a new one, to save." : "Each alert is POSTed here. A 2xx answer counts as delivered; 429, 408 and 5xx are retried for 24 hours."}</FieldDescription>
      </Field>
      {configured && (
        <Field orientation="horizontal">
          <Checkbox id="alert-webhook-rotate" checked={rotate} onCheckedChange={(v) => setRotate(v === true)} />
          <FieldLabel htmlFor="alert-webhook-rotate" className="font-normal">
            Make a new signing secret
          </FieldLabel>
        </Field>
      )}
      <Button type="submit" size="sm" disabled={!valid}>
        {configured ? "Save" : "Add webhook"}
      </Button>
      {save.isError && (
        <p role="alert" className="text-[13px] text-destructive">
          {errorText(save.error)}
        </p>
      )}
      {secret && (
        <div ref={shown} className="space-y-2">
          <p className="text-[13px] text-muted-foreground">Copy the signing secret now. It is not shown again. Verify each delivery's webhook-signature header with it.</p>
          <CopyCommand text={secret} label="Signing secret" copyLabel="Copy secret" />
          <Button type="button" size="sm" variant="outline" onClick={() => setSecret(null)}>
            Done
          </Button>
        </div>
      )}
    </form>
  )
}

const splitRecipients = (s: string) => [...new Set(s.split(/[\s,;]+/).map((x) => x.trim().toLowerCase()).filter(Boolean))]

function EmailForm({ recipients }: { recipients: string[] }) {
  const [text, setText] = useState<string | null>(null)
  const value = text ?? recipients.join("\n")
  const list = splitRecipients(value)
  const save = useSave("email", () => setText(null))
  const bad = list.filter((x) => !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(x))
  const valid = list.length > 0 && list.length <= 20 && bad.length === 0 && !save.isPending
  return (
    <form
      className="space-y-3"
      aria-label="Email"
      onSubmit={(e) => {
        e.preventDefault()
        if (valid) save.mutate({ recipients: list })
      }}
    >
      <Field>
        <FieldLabel htmlFor="alert-email-recipients">Recipients</FieldLabel>
        <Textarea id="alert-email-recipients" rows={3} placeholder="oncall@company.com" value={value} onChange={(e) => setText(e.target.value)} />
        <FieldDescription>{bad.length ? `Not an address: ${bad.join(", ")}` : "One to 20 addresses, one per line. Each alert goes to all of them as one email."}</FieldDescription>
      </Field>
      <Button type="submit" size="sm" disabled={!valid || (text === null && recipients.length > 0)}>
        {recipients.length ? "Save" : "Add email"}
      </Button>
      {save.isError && (
        <p role="alert" className="text-[13px] text-destructive">
          {errorText(save.error)}
        </p>
      )}
    </form>
  )
}

// A chat channel's incoming webhook URL, entered on every save: the saved one is sealed and never
// comes back.
function UrlForm({ kind, name, placeholder, how, configured }: { kind: AlertChannelKind; name: string; placeholder: string; how: string; configured: boolean }) {
  const [url, setUrl] = useState("")
  const save = useSave(kind, () => setUrl(""))
  const valid = /^https:\/\/[^\s/]+\/\S+$/.test(url.trim()) && !save.isPending
  const id = `alert-${kind}-url`
  return (
    <form
      className="space-y-3"
      aria-label={name}
      onSubmit={(e) => {
        e.preventDefault()
        if (valid) save.mutate({ url: url.trim() })
      }}
    >
      <Field>
        <FieldLabel htmlFor={id}>{configured ? "Replace the webhook URL" : "Webhook URL"}</FieldLabel>
        <InputGroup>
          <InputGroupAddon>
            <LinkSimple />
          </InputGroupAddon>
          <InputGroupInput id={id} type="url" inputMode="url" autoComplete="off" placeholder={placeholder} value={url} onChange={(e) => setUrl(e.target.value)} />
        </InputGroup>
        <FieldDescription>{configured ? "The saved URL is never shown again. Enter it, or a new one, to save." : how}</FieldDescription>
      </Field>
      <Button type="submit" size="sm" disabled={!valid}>
        {configured ? "Save" : `Add ${name}`}
      </Button>
      {save.isError && (
        <p role="alert" className="text-[13px] text-destructive">
          {errorText(save.error)}
        </p>
      )}
    </form>
  )
}

// A paging channel's key and region. The key is entered on every save: the saved one is sealed and
// never comes back.
function KeyForm({ kind, name, label, how, configured }: { kind: AlertChannelKind; name: string; label: string; how: string; configured: boolean }) {
  const [key, setKey] = useState("")
  const [region, setRegion] = useState<"us" | "eu">("us")
  const save = useSave(kind, () => setKey(""))
  const valid = key.trim().length >= 8 && !save.isPending
  const id = `alert-${kind}-key`
  return (
    <form
      className="space-y-3"
      aria-label={name}
      onSubmit={(e) => {
        e.preventDefault()
        if (valid) save.mutate({ key: key.trim(), region })
      }}
    >
      <Field>
        <FieldLabel htmlFor={id}>{configured ? `Replace the ${label.toLowerCase()}` : label}</FieldLabel>
        <InputGroup>
          <InputGroupAddon>
            <Key />
          </InputGroupAddon>
          <InputGroupInput id={id} type="password" autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)} />
        </InputGroup>
        <FieldDescription>{configured ? "The saved key is never shown again. Enter it, or a new one, to save." : how}</FieldDescription>
      </Field>
      <Field>
        <FieldLabel id={`${id}-region`}>Region</FieldLabel>
        <ToggleGroup type="single" variant="outline" size="sm" value={region} onValueChange={(v) => v && setRegion(v as "us" | "eu")} aria-labelledby={`${id}-region`} className="w-fit">
          <ToggleGroupItem value="us">US</ToggleGroupItem>
          <ToggleGroupItem value="eu">EU</ToggleGroupItem>
        </ToggleGroup>
      </Field>
      <Button type="submit" size="sm" disabled={!valid}>
        {configured ? "Save" : `Add ${name}`}
      </Button>
      {save.isError && (
        <p role="alert" className="text-[13px] text-destructive">
          {errorText(save.error)}
        </p>
      )}
    </form>
  )
}
