import { useEffect, useRef, useState } from "react"
import { Link } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { motion, useReducedMotion } from "motion/react"
import { ArrowSquareOut, Check, Copy } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { NumberTicker } from "@/components/ui/number-ticker"
import { Ripple } from "@/components/ui/ripple"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { CONNECTION_POLL_MS, callQuery, entitlementQuery, latestCallQuery } from "@/lib/api"
import type { ProjectConnection } from "@/lib/api-types"
import { sourceState } from "@/features/connection/connection-model"
import { cn } from "@/lib/utils"
import { agentConnected, agentProblem, firstLook, planText, secs } from "./setup-model"
import { Eyebrow, Pill, Rise, Subtitle, Surface, Title } from "./setup-ui"

// A code block with its own copy button. Long lines scroll sideways inside the block instead of
// breaking a URL mid-token; the page itself never scrolls sideways.
function CodeBlock({ label, code, note }: { label: string; code: string; note?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="space-y-1.5">
      <div className="min-w-0 overflow-hidden rounded-[22px] bg-neutral-900 text-neutral-100">
        <div className="flex items-center justify-between border-b border-white/10 py-1 pr-1.5 pl-5 sm:pl-6">
          <span className="text-[12px] text-neutral-400">{label}</span>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={copied ? "Copied" : `Copy ${label.toLowerCase()}`}
            className="text-neutral-300 hover:bg-white/10 hover:text-white"
            onClick={() =>
              navigator.clipboard
                ?.writeText(code)
                .then(() => {
                  setCopied(true)
                  setTimeout(() => setCopied(false), 2000)
                })
                .catch(() => {})
            }
          >
            {copied ? <Check /> : <Copy />}
          </Button>
        </div>
        <pre aria-label={label} tabIndex={0} className="overflow-x-auto px-5 py-4 font-mono text-[13px] leading-relaxed whitespace-pre outline-none focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset sm:px-6 sm:py-5 sm:text-[15px]">
          {code}
        </pre>
      </div>
      {note && <p className="pl-1 text-[13px] text-muted-foreground">{note}</p>}
    </div>
  )
}

// Screen 2: the one variable the agent needs, and its status from getProjectConnection (read again
// every 5 s by the caller). Connected when the agent's upload, or an SDK batch, was accepted.
export function AgentScreen({ connection: c, now, onContinue }: { connection: ProjectConnection; now: number; onContinue: () => void }) {
  const [lang, setLang] = useState<"python" | "node">("python")
  const [late, setLate] = useState(false)
  const connected = agentConnected(c, now)
  const agent = sourceState(c.agent, now)
  const fix = agent.state === "error" ? agentProblem(c) : null
  useEffect(() => {
    const t = setTimeout(() => setLate(true), 20_000)
    return () => clearTimeout(t)
  }, [])
  // Becoming connected on this screen moves on by itself after a beat, so the green row is seen. A
  // screen opened already connected (going back to it) stays put until Continue.
  const connectedAtOpen = useRef(connected)
  useEffect(() => {
    if (!connected || connectedAtOpen.current) return
    const t = setTimeout(onContinue, 1500)
    return () => clearTimeout(t)
  }, [connected, onContinue])
  const upload = c.urls.observability
  return (
    <div className="space-y-10">
      <Rise className="space-y-4">
        <Title>Point your agent here.</Title>
        <Subtitle>Put this variable in your agent's .env, and turn on recording where it starts the session. Your agent sends each session here when it ends.</Subtitle>
      </Rise>
      <Rise delay={0.1} className="space-y-5">
        {upload === null ? (
          <Surface>
            <p className="text-[15px]">The upload URL is not available on this instance yet.</p>
          </Surface>
        ) : (
          <CodeBlock label=".env" code={`LIVEKIT_OBSERVABILITY_URL=${upload}`} />
        )}
        <div className="space-y-3">
          <ToggleGroup type="single" variant="outline" value={lang} onValueChange={(v) => v && setLang(v as "python" | "node")} aria-label="Agent language">
            <ToggleGroupItem value="python">Python</ToggleGroupItem>
            <ToggleGroupItem value="node">Node</ToggleGroupItem>
          </ToggleGroup>
          {lang === "python" ? (
            <CodeBlock label="Python session start" code="await session.start(room=ctx.room, agent=agent, record=True)" note="livekit-agents 1.8.6 or later." />
          ) : (
            <CodeBlock label="Node session start" code="await session.start({ room: ctx.room, agent, record: true })" note="@livekit/agents 1.8.0 or later." />
          )}
        </div>
      </Rise>
      <Rise delay={0.2} className="space-y-3">
        <div role="status" className="flex items-start gap-3 rounded-[22px] bg-muted px-5 py-4">
          <span className="relative mt-1.5 flex size-3 shrink-0">
            {!connected && agent.state !== "error" && <span className="absolute inline-flex size-full animate-ping rounded-full bg-primary opacity-60 motion-reduce:hidden" />}
            <span className={cn("relative inline-flex size-3 rounded-full", connected ? "bg-emerald-500" : agent.state === "error" ? "bg-destructive" : "bg-primary")} />
          </span>
          <div className="min-w-0 space-y-1">
            <p className="text-[16px] font-medium">{connected ? "Your agent is connected" : fix ? fix.title : late ? "Restart your agent with the new variable, then start a session." : "Waiting for your agent"}</p>
            {fix && (
              <Collapsible>
                <p className="text-[14px] text-muted-foreground">{fix.summary}</p>
                {fix.details && (
                  <>
                    <CollapsibleTrigger asChild>
                      <Button variant="link" size="sm" className="h-auto px-0 text-foreground underline underline-offset-4">
                        Details
                      </Button>
                    </CollapsibleTrigger>
                    <CollapsibleContent>
                      <p className="text-[14px] break-words text-muted-foreground">{fix.details}</p>
                    </CollapsibleContent>
                  </>
                )}
              </Collapsible>
            )}
          </div>
        </div>
        <Collapsible className="rounded-[22px] border px-5 py-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[15px]">
              <span className="font-medium">Recommended: add a LiveKit webhook.</span> <span className="text-muted-foreground">Adds rooms, phone calls and egress. You can do it later in Settings.</span>
            </p>
            <CollapsibleTrigger asChild>
              <Button variant="link" className="h-auto px-0">
                Show me how
              </Button>
            </CollapsibleTrigger>
          </div>
          <CollapsibleContent className="space-y-2 pt-3">
            <p className="text-[14px] text-muted-foreground">In LiveKit Cloud, open Settings, then Webhooks. Add this URL and pick this project's API key.</p>
            {c.urls.webhook === null ? <p className="text-[14px] text-muted-foreground">The webhook URL is not available on this instance yet.</p> : <CodeBlock label="Webhook URL" code={c.urls.webhook} />}
          </CollapsibleContent>
        </Collapsible>
      </Rise>
      {connected && (
        <Rise>
          <Pill onClick={onContinue}>Continue</Pill>
        </Rise>
      )}
    </div>
  )
}

const states = ["listening", "thinking", "speaking"] as const

// Screen 3: the project waits for its first call, made with LiveKit's own Agents Playground. The
// newest call is read every 5 s and an open one every 2 s for its live state; the chips follow the
// agent's state, and the transcript shows only the turn text getCall carries.
export function CallScreen() {
  const latest = useQuery({ ...latestCallQuery(), refetchInterval: CONNECTION_POLL_MS })
  const open = latest.data?.endState === "open" ? latest.data.id : null
  const detail = useQuery({ ...callQuery(open ?? ""), enabled: open !== null, refetchInterval: 2_000 })
  const agentState = detail.data?.live?.agent?.state ?? null
  // Only what getCall carries: the turns' text, when the call reports it while it runs. No turns,
  // no bubbles; the chips still follow the live state.
  const lines = (detail.data?.turns ?? []).flatMap((t) => [...(t.userText?.trim() ? [{ who: "Caller", text: t.userText }] : []), ...(t.agentText?.trim() ? [{ who: "Agent", text: t.agentText }] : [])]).slice(-3)
  const hearing = open !== null
  return (
    <div className="space-y-10">
      <Rise className="space-y-4">
        <Title>{hearing ? "We can hear it." : "Now, call your agent."}</Title>
        <Subtitle>{hearing ? "Your call is live. When it ends, the first look opens here." : "Talk to it for a few seconds in LiveKit's Agents Playground. This page moves on by itself."}</Subtitle>
      </Rise>
      <Rise delay={0.1}>
        <div className="relative flex h-64 items-center justify-center overflow-hidden rounded-[22px] bg-muted sm:h-72">
          <Ripple mainCircleSize={96} numCircles={3} className="motion-reduce:hidden" />
          <span className={cn("z-10 size-20 rounded-full bg-primary shadow-lg", hearing ? "motion-safe:animate-pulse" : "")} aria-hidden="true" />
          <p role="status" className="sr-only">
            {hearing ? `A call is live${agentState ? `; the agent is ${agentState}` : ""}.` : "Waiting for your first call."}
          </p>
        </div>
      </Rise>
      {hearing && (
        <Rise className="space-y-4">
          <ul aria-label="Agent state" className="flex flex-wrap gap-2">
            {states.map((s) => (
              <li key={s} className={cn("rounded-full px-4 py-1.5 text-[15px] capitalize transition-colors", agentState === s ? "bg-primary font-medium text-primary-foreground" : "bg-muted text-muted-foreground")}>
                {s}
              </li>
            ))}
          </ul>
          {lines.length > 0 && (
            <ul aria-label="Transcript" className="space-y-2">
              {lines.map((l, k) => (
                <li key={k} className={cn("w-fit max-w-[80%] rounded-2xl px-4 py-2 text-[15px]", l.who === "Agent" ? "ml-auto bg-accent" : "bg-muted")}>
                  <span className="sr-only">{`${l.who}: `}</span>
                  {l.text}
                </li>
              ))}
            </ul>
          )}
        </Rise>
      )}
      <Rise delay={0.2}>
        <Pill asChild>
          <a href="https://agents-playground.livekit.io" target="_blank" rel="noreferrer">
            Open LiveKit Agents Playground <ArrowSquareOut />
          </a>
        </Pill>
      </Rise>
    </div>
  )
}

// Screen 4: the real first call. Its first measured turn, the slowest of its known stages in blue,
// the turn and interruption counts, and the plan from the entitlement read. Unknown stays unknown.
export function FirstLookScreen({ callId }: { callId: string }) {
  const reduced = useReducedMotion()
  const read = useQuery(callQuery(callId))
  const plan = planText(useQuery(entitlementQuery()).data)
  const f = read.data ? firstLook(read.data) : null
  if (!f) return <p className="text-[17px] text-muted-foreground">{read.isError ? "The call could not be read." : "Opening your first call."}</p>
  const total = f.answerMs
  const scale = Math.max(total ?? 0, ...f.stages.map((s) => s.ms), 1)
  return (
    <div className="space-y-10">
      <Rise className="space-y-4">
        <Eyebrow tone="success">Your first call is in.</Eyebrow>
        <Title>
          {total === null ? (
            "Your agent answered."
          ) : (
            <>
              Your agent answered in{" "}
              {reduced ? (
                (total / 1000).toFixed(2)
              ) : (
                <NumberTicker value={total / 1000} decimalPlaces={2} className="tracking-[-0.025em] text-foreground dark:text-foreground" />
              )}{" "}
              s.
            </>
          )}
        </Title>
        <Subtitle>
          {total === null
            ? "No turn measured its answer time, so the breakdown is unknown."
            : f.slowest
              ? `${f.slowest.ms > total / 2 ? "Most of the wait was" : "The biggest part was"} ${f.slowest.name}: ${secs(f.slowest.ms)} of ${secs(total)}.`
              : "This turn reported no stage times, so the breakdown is unknown."}
        </Subtitle>
      </Rise>
      {f.stages.length > 0 && (
        <Rise delay={0.1}>
          <Surface className="space-y-4" aria-label={`Turn ${f.turn}`} role="figure">
            <p className="text-[13px] font-medium text-muted-foreground">{`Turn ${f.turn}${f.answerDerived ? ", timed from the recording" : ""}`}</p>
            {f.stages.map((s, k) => (
              <div key={s.name} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)_4.5rem]">
                <span className="text-[14px] first-letter:uppercase">{s.name}</span>
                <span className="text-right text-[14px] tabular-nums sm:order-last">{secs(s.ms)}</span>
                <div className="col-span-2 h-3 overflow-hidden rounded-full bg-background sm:col-span-1">
                  <motion.div className={cn("h-full rounded-full", s === f.slowest ? "bg-primary" : "bg-muted-foreground/40")} initial={reduced ? false : { width: 0 }} animate={{ width: `${(s.ms / scale) * 100}%` }} transition={{ duration: 0.7, delay: 0.3 + k * 0.25, ease: "easeOut" }} />
                </div>
              </div>
            ))}
          </Surface>
        </Rise>
      )}
      <Rise delay={0.2} className="grid gap-3 sm:grid-cols-3">
        {[
          ["Turns", f.turns === null ? "Unknown" : String(f.turns)],
          ["Interruptions", f.interruptions === null ? "Unknown" : String(f.interruptions)],
          ["Your plan", plan ?? "Unknown"],
        ].map(([label, value]) => (
          <Surface key={label} className="space-y-1">
            <p className="text-[13px] text-muted-foreground">{label}</p>
            <p className={cn("text-[24px] font-semibold tracking-[-0.01em]", value === "Unknown" && "text-muted-foreground")}>{value}</p>
          </Surface>
        ))}
      </Rise>
      <Rise delay={0.3} className="flex flex-wrap items-center gap-3">
        <Pill asChild>
          <Link to="/calls/$callId" params={{ callId }}>
            Open this call
          </Link>
        </Pill>
        <Pill variant="ghost" asChild>
          <Link to="/calls">Go to all calls</Link>
        </Pill>
      </Rise>
    </div>
  )
}
