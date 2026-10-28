import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { EnvelopeSimple, LinkSimple, PaperPlaneTilt, Trash, WebhooksLogo } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Item, ItemContent, ItemDescription, ItemGroup, ItemSeparator, ItemTitle } from "@/components/ui/item"
import { Textarea } from "@/components/ui/textarea"
import { ProviderLogo } from "@/components/provider-logo"
import { CopyCommand } from "@/components/copy-command"
import { errorText } from "@/components/page-state"
import { fmtHourMinute } from "@/lib/format"
import { alertSetupQuery, removeAlertChannel, saveAlertChannel, testAlertChannel } from "@/lib/api"
import type { AlertChannel, AlertChannelKind, AlertChannelUpdate, AlertDelivery } from "@/lib/api-types"

// Where this project's alerts go (alerts-behaviour.md, Delivery). Email and webhook deliver today;
// an Admin sets them here and the server seals the webhook URL and signing secret, so the browser
// sees only the host, and the secret once, in the answer that made it.

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

const coming = [
  { logo: "slack", name: "Slack" },
  { logo: "teams", name: "Microsoft Teams" },
  { logo: "discord", name: "Discord" },
  { logo: "pagerduty", name: "PagerDuty" },
  { logo: "opsgenie", name: "Opsgenie" },
]

export function AlertChannels({ isAdmin, selfHosted }: { isAdmin: boolean; selfHosted: boolean }) {
  const read = useQuery(alertSetupQuery())
  const channels = read.data?.channels ?? []
  const find = (k: AlertChannelKind) => channels.find((c) => c.kind === k)
  return (
    <ItemGroup className="gap-0 has-data-[size=sm]:gap-0">
      <ChannelItem kind="webhook" icon={WebhooksLogo} name="Webhook" description="POST each alert as JSON, signed with Standard Webhooks." channel={find("webhook")} isAdmin={isAdmin} loaded={read.isSuccess}>
        <WebhookForm configured={Boolean(find("webhook"))} />
      </ChannelItem>
      <ItemSeparator className="my-0" />
      <ChannelItem kind="email" icon={EnvelopeSimple} name="Email" description="Send each alert to a list of addresses." channel={find("email")} isAdmin={isAdmin} loaded={read.isSuccess}>
        {read.data?.emailAvailable ? (
          <EmailForm recipients={find("email")?.recipients ?? []} />
        ) : (
          <p className="text-[13px] text-muted-foreground">{selfHosted ? "This server sends no email. Set VOICEGATEWAY_SMTP_URL on the server, then add recipients here." : "Email is not available on this workspace yet."}</p>
        )}
      </ChannelItem>
      {coming.map((x) => (
        <span key={x.name} className="contents">
          <ItemSeparator className="my-0" />
          <Item role="listitem" size="sm" className="rounded-none border-0 px-0 py-4">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-lg border bg-background opacity-60 grayscale">
              <ProviderLogo provider={x.logo} size={20} />
            </span>
            <ItemContent className="min-w-0 gap-0.5">
              <ItemTitle className="text-[14px] font-medium">{x.name}</ItemTitle>
              <ItemDescription className="text-[13px]">Coming soon. A webhook reaches it today through its incoming webhook URL.</ItemDescription>
            </ItemContent>
            <Badge variant="outline" className="shrink-0 font-normal text-muted-foreground">
              Coming soon
            </Badge>
          </Item>
        </span>
      ))}
    </ItemGroup>
  )
}

function ChannelItem({ kind, icon: I, name, description, channel, isAdmin, loaded, children }: { kind: AlertChannelKind; icon: Icon; name: string; description: string; channel?: AlertChannel; isAdmin: boolean; loaded: boolean; children: React.ReactNode }) {
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
          <I size={20} />
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
              <DialogDescription>
                {kind === "webhook"
                  ? "The URL and signing secret are deleted and cannot be shown again. Adding the webhook again makes a new secret, so every receiver needs it. Rules stop sending here."
                  : "The recipients are deleted. Rules stop sending email until it is set up again."}
              </DialogDescription>
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
