import { useEffect, useState } from "react"
import { motion, useReducedMotion } from "motion/react"
import { ArrowSquareOut, CheckCircle, Circle, ClipboardText, XCircle } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Kbd, KbdGroup } from "@/components/ui/kbd"
import { Spinner } from "@/components/ui/spinner"
import { errorText } from "@/components/page-state"
import { ApiError, createProject } from "@/lib/api"
import type { ProjectCreated } from "@/lib/api-types"
import { type LiveKitEnv, namesLiveKit, parseLiveKitEnv, pasteLiveKit } from "@/lib/livekit-env"
import { cn } from "@/lib/utils"
import { maskedKey, projectLabel, projectName, roomsLine } from "./setup-model"
import { Eyebrow, Pill, Rise, Subtitle, Surface, Title } from "./setup-ui"

// Screen 0: what VoiceGateway shows, before asking for anything. The card is an illustration of one
// turn, labelled as an example; it carries no numbers.
export function WelcomeScreen({ onStart }: { onStart: () => void }) {
  const reduced = useReducedMotion()
  const bars = [
    { label: "Hearing", width: "38%", tone: "bg-muted-foreground/40" },
    { label: "Thinking", width: "62%", tone: "bg-primary" },
    { label: "Speaking", width: "30%", tone: "bg-muted-foreground/40" },
  ]
  return (
    <div className="space-y-10">
      <Rise className="space-y-4">
        <Eyebrow>VoiceGateway Cloud</Eyebrow>
        <Title>See inside every voice call.</Title>
        <Subtitle>Connect your LiveKit project and watch your next call, from the phone line to the model. About two minutes.</Subtitle>
      </Rise>
      <Rise delay={0.15}>
        <Surface role="img" aria-label="Example call, one turn: hearing, thinking and speaking, one after another." className="space-y-4">
          <p className="text-[13px] font-medium text-muted-foreground">Example call, one turn</p>
          <div className="space-y-3">
            {bars.map((b, k) => (
              <div key={b.label} className="grid grid-cols-[76px_minmax(0,1fr)] items-center gap-3">
                <span className="text-[14px] font-medium">{b.label}</span>
                <div className="h-3 overflow-hidden rounded-full bg-background">
                  <motion.div className={cn("h-full rounded-full", b.tone)} style={{ marginLeft: k === 0 ? 0 : k === 1 ? "38%" : "70%" }} initial={reduced ? false : { width: 0 }} animate={{ width: b.width }} transition={{ duration: 0.7, delay: 0.3 + k * 0.35, ease: "easeOut" }} />
                </div>
              </div>
            ))}
          </div>
        </Surface>
      </Rise>
      <Rise delay={0.3}>
        <Pill onClick={onStart}>Connect LiveKit</Pill>
      </Rise>
    </div>
  )
}

type Check = "waiting" | "running" | "ok" | "failed"

function CheckRow({ state, label, index }: { state: Check; label: string; index: number }) {
  const reduced = useReducedMotion()
  return (
    <motion.li
      initial={reduced ? false : { opacity: 0, x: -6 }}
      animate={{ opacity: state === "waiting" ? 0.5 : 1, x: 0 }}
      transition={{ duration: 0.3, delay: reduced ? 0 : index * 0.45 }}
      className="flex items-center gap-3 text-[16px]"
    >
      {state === "running" ? <Spinner className="size-5 text-primary" /> : state === "ok" ? <CheckCircle weight="fill" className="size-5 text-primary" /> : state === "failed" ? <XCircle weight="fill" className="size-5 text-destructive" /> : <Circle className="size-5 text-muted-foreground" />}
      <span>{label}</span>
    </motion.li>
  )
}

// What a refusal means, in one line, and what to do.
function refusal(error: unknown): { line: string; fix: string } {
  const code = error instanceof ApiError ? error.code : null
  if (code === "livekit_secret_invalid") return { line: "LiveKit did not accept this key and secret.", fix: "Create a new key in LiveKit Cloud and paste its environment variables again." }
  if (code === "livekit_unreachable") return { line: "LiveKit did not answer at this URL.", fix: "Check LIVEKIT_URL; it looks like wss://your-project.livekit.cloud." }
  if (code === "project_exists") return { line: "This organisation already has a project with that name.", fix: "Open it from the project menu, or add this one after renaming the other in Settings." }
  return { line: errorText(error), fix: "Try again in a moment." }
}

const typing = (el: EventTarget | null) => el instanceof HTMLElement && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))
const suffix = () => Math.random().toString(36).slice(2, 6)

type Run = { state: "pending" } | { state: "ok"; project: ProjectCreated } | { state: "failed"; error: unknown }

// Screen 1: the LiveKit project's environment variables, pasted on the target or anywhere on the page
// outside another text field, or typed under "Enter them yourself". A complete set is sent at once.
// createProject verifies the key with one signed LiveKit read; the rows follow its answer, and the
// third names the live room count it read (liveRooms, null when not counted). The name is the LiveKit
// subdomain; a taken name is retried once with a short suffix. The secret leaves the inputs as soon
// as it is sent and is never shown.
export function ConnectScreen({ orgId, onContinue }: { orgId: string; onContinue: (project: ProjectCreated) => void }) {
  const [url, setUrl] = useState("")
  const [key, setKey] = useState("")
  const [secret, setSecret] = useState("")
  const [manual, setManual] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [sent, setSent] = useState<{ url: string; key: string } | null>(null)

  const send = async (u: string, k: string, s: string) => {
    setSecret("")
    setSent({ url: u, key: k })
    setRun({ state: "pending" })
    const name = projectName(u)
    const body = { livekitUrl: u, livekitApiKey: k, livekitApiSecret: s }
    try {
      let project: ProjectCreated
      try {
        project = await createProject(orgId, { name, ...body })
      } catch (e) {
        if (!(e instanceof ApiError && e.code === "project_exists")) throw e
        project = await createProject(orgId, { name: `${name}-${suffix()}`, ...body })
      }
      setRun({ state: "ok", project })
    } catch (error) {
      setRun({ state: "failed", error })
    }
  }

  const fill = (env: LiveKitEnv) => {
    if (!namesLiveKit(env)) return setNote("Nothing to use there. Copy the environment variables of a LiveKit API key.")
    const u = env.url ?? url
    const k = env.apiKey ?? key
    const s = env.apiSecret ?? secret
    setUrl(u)
    setKey(k)
    setSecret(s)
    if (u.trim() && k.trim() && s.trim()) {
      setNote(null)
      void send(u.trim(), k.trim(), s)
    } else {
      setManual(true)
      setNote("Some of the three are missing. Fill in the rest below.")
    }
  }

  useEffect(() => {
    if (run) return
    const listen = (e: ClipboardEvent) => {
      if (typing(e.target) || !e.clipboardData) return
      const env = parseLiveKitEnv(e.clipboardData.getData("text"))
      if (!namesLiveKit(env)) return
      e.preventDefault()
      fill(env)
    }
    document.addEventListener("paste", listen)
    return () => document.removeEventListener("paste", listen)
  })

  const header = (
    <Rise className="space-y-4">
      <Title>Bring your LiveKit project.</Title>
      <Subtitle>In LiveKit Cloud, open Settings, then API keys. Create a key and copy its environment variables.</Subtitle>
    </Rise>
  )

  if (run && sent) {
    const checks: [Check, Check, Check] =
      run.state === "pending"
        ? ["running", "waiting", "waiting"]
        : run.state === "ok"
          ? ["ok", "ok", "ok"]
          : run.error instanceof ApiError && run.error.code === "livekit_unreachable"
            ? ["failed", "waiting", "waiting"]
            : run.error instanceof ApiError && run.error.code === "livekit_secret_invalid"
              ? ["ok", "failed", "waiting"]
              : ["waiting", "waiting", "waiting"]
    const problem = run.state === "failed" ? refusal(run.error) : null
    return (
      <div className="space-y-10">
        {header}
        <Rise delay={0.1}>
          <Surface className="space-y-6" aria-live="polite">
            <dl className="grid gap-4 sm:grid-cols-3">
              <div className="min-w-0">
                <dt className="text-[13px] text-muted-foreground">LiveKit project</dt>
                <dd className="font-mono text-[15px] break-all">{projectLabel(sent.url)}</dd>
              </div>
              <div className="min-w-0">
                <dt className="text-[13px] text-muted-foreground">API key</dt>
                <dd className="font-mono text-[15px] break-all">{maskedKey(sent.key)}</dd>
              </div>
              <div className="min-w-0">
                <dt className="text-[13px] text-muted-foreground">API secret</dt>
                <dd className="font-mono text-[15px]">
                  <span aria-hidden="true">••••••••••</span>
                  <span className="sr-only">hidden</span>
                </dd>
              </div>
            </dl>
            <ul aria-label="Checks" className="space-y-3">
              <CheckRow state={checks[0]} label="Reaching your LiveKit project" index={0} />
              <CheckRow state={checks[1]} label="Key verified" index={1} />
              <CheckRow state={checks[2]} label={run.state === "ok" ? roomsLine(run.project.liveRooms) : "Counting live rooms"} index={2} />
            </ul>
            {problem && (
              <div role="alert" className="space-y-1">
                <p className="text-[15px] font-medium text-destructive">{problem.line}</p>
                <p className="text-[14px] text-muted-foreground">{problem.fix}</p>
              </div>
            )}
          </Surface>
        </Rise>
        {run.state === "ok" && (
          <Rise delay={0.9}>
            <Pill onClick={() => onContinue(run.project)}>Continue</Pill>
          </Rise>
        )}
        {run.state === "failed" && (
          <Pill
            variant="outline"
            onClick={() => {
              setRun(null)
              setSent(null)
              setNote(null)
            }}
          >
            Try again
          </Pill>
        )}
      </div>
    )
  }

  const onPaste = pasteLiveKit(fill)
  return (
    <div className="space-y-8">
      {header}
      <Rise delay={0.1}>
        <label className="relative flex h-[220px] cursor-text flex-col items-center justify-center gap-3 rounded-[22px] border-2 border-dashed border-border bg-muted text-center transition-colors focus-within:border-primary hover:border-primary/60">
          <ClipboardText className="size-9 text-primary" />
          <span className="text-[21px] font-semibold">Paste your LiveKit keys</span>
          <KbdGroup>
            <Kbd>⌘</Kbd>
            <Kbd>V</Kbd>
          </KbdGroup>
          <textarea
            aria-label="Paste your LiveKit keys"
            className="absolute inset-0 size-full cursor-text resize-none rounded-[22px] opacity-0"
            value=""
            onChange={() => {}}
            onPaste={(e) => {
              e.preventDefault()
              fill(parseLiveKitEnv(e.clipboardData.getData("text")))
            }}
          />
        </label>
        {note && (
          <p role="status" className="pt-3 text-[14px] text-muted-foreground">
            {note}
          </p>
        )}
      </Rise>
      <Rise delay={0.2} className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <Button variant="link" className="h-auto px-0 text-[15px]" asChild>
          <a href="https://cloud.livekit.io" target="_blank" rel="noreferrer">
            Open LiveKit Cloud <ArrowSquareOut />
          </a>
        </Button>
        <Collapsible open={manual} onOpenChange={setManual} className="contents">
          <CollapsibleTrigger asChild>
            <Button variant="link" className="h-auto px-0 text-[15px] text-muted-foreground">
              Enter them yourself
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="w-full">
            <form
              aria-label="Enter your LiveKit keys"
              className="pt-2"
              onSubmit={(e) => {
                e.preventDefault()
                if (url.trim() && key.trim() && secret.trim()) void send(url.trim(), key.trim(), secret)
              }}
            >
              <FieldGroup className="gap-4">
                <Field>
                  <FieldLabel htmlFor="setup-url">LiveKit URL</FieldLabel>
                  <Input id="setup-url" placeholder="wss://your-project.livekit.cloud" value={url} onChange={(e) => setUrl(e.target.value)} onPaste={onPaste} autoComplete="off" spellCheck={false} />
                </Field>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field>
                    <FieldLabel htmlFor="setup-key">API key</FieldLabel>
                    <Input id="setup-key" value={key} onChange={(e) => setKey(e.target.value)} onPaste={onPaste} autoComplete="off" spellCheck={false} />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="setup-secret">API secret</FieldLabel>
                    <Input id="setup-secret" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} onPaste={onPaste} autoComplete="new-password" />
                  </Field>
                </div>
                <div>
                  <Pill type="submit" disabled={!(url.trim() && key.trim() && secret.trim())}>
                    Connect
                  </Pill>
                </div>
              </FieldGroup>
            </form>
          </CollapsibleContent>
        </Collapsible>
      </Rise>
    </div>
  )
}
