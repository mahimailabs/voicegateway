import { useEffect, useRef, useState } from "react"
import { CaretRight, Flask, Info, MagnifyingGlass, X, LockSimpleOpen, PhoneIncoming, PhoneOutgoing, ShieldWarning, Timer } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { CategoryBar } from "@/components/tremor/CategoryBar"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from "@/components/ui/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { useQuery } from "@tanstack/react-query"
import { QueryState } from "@/components/page-state"
import { sipQuery } from "@/lib/api"
// A hand-written layout example (?data=test), not API data: it shows the page with many trunks.
import synthetic from "@/mocks/sip-test.json"
import { type Room, RoomDrawer, data } from "../media-server/room-drawer"
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput, InputGroupText } from "@/components/ui/input-group"
import { Kbd } from "@/components/ui/kbd"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { PathDiagram, PathLegend } from "./call-path"
import { type Check, type Outbound, type Route, type SipData, checks, enc, inPaths, inboundPath, outboundPath, providerOf, routes, transport } from "./sip-model"
import { useUrlPick, useUrlState } from "@/lib/url-state"

// +1 numbers read as (705) 710-0749; anything else stays in E.164.
const pretty = (n: string) => (/^\+1\d{10}$/.test(n) ? `+1 (${n.slice(2, 5)}) ${n.slice(5, 8)}-${n.slice(8)}` : n)
type NumbersFile = { inbound: Record<string, string[]>; outbound: Record<string, string[]>; livekit: string[] }
const SHOW = 3

// A trunk can carry hundreds of numbers. Show a few inline and the rest in a searchable popover, so
// a big trunk never pushes the call path or the stat cards out of shape.
type Num = { key: string; label: string; hint?: string; bad?: string; match?: boolean }
function NumberList({ items, max = SHOW, prefix = "", small }: { items: Num[]; max?: number; prefix?: string; small?: boolean }) {
  // Numbers match by digits in order, so "2031" never finds 2013 the way fuzzy search would.
  const [find, setFind] = useState("")
  const want = find.replace(/\D/g, "")
  const found = want ? items.filter((n) => n.label.replace(/\D/g, "").includes(want)) : items
  const chip = (n: Num) => (
    <span className={cn("rounded-md font-mono whitespace-nowrap ring-1 ring-border", small ? "bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground ring-0" : "bg-background px-1.5 py-0.5 text-[12px]", n.match && "ring-2 ring-primary")}>
      {prefix}
      {n.label}
      {n.bad && <span className="ml-1 text-destructive">{n.bad}</span>}
    </span>
  )
  // A match from the search box is always shown inline, even past the first few.
  const inline = [...items.filter((n) => n.match), ...items.filter((n) => !n.match)].slice(0, Math.max(max, items.filter((n) => n.match).length))
  const rest = items.length - inline.length
  return (
    <>
      {inline.map((n) =>
        n.hint ? (
          <Tooltip key={n.key}>
            <TooltipTrigger asChild>
              <span className="cursor-help">{chip(n)}</span>
            </TooltipTrigger>
            <TooltipContent className="max-w-72 text-xs">{n.hint}</TooltipContent>
          </Tooltip>
        ) : (
          <span key={n.key}>{chip(n)}</span>
        ),
      )}
      {rest > 0 && (
        <Popover>
          <PopoverTrigger asChild>
            <button type="button" className={cn("rounded-md px-1.5 py-0.5 whitespace-nowrap text-primary hover:bg-primary/10", small ? "text-[11px]" : "text-[12px]")}>
              +{rest} more
            </button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-64 p-0">
            <Command shouldFilter={false}>
              <CommandInput placeholder={`Find among ${items.length}`} value={find} onValueChange={setFind} inputMode="tel" />
              <CommandList className="max-h-64">
                <CommandEmpty>No number matches.</CommandEmpty>
                {found.map((n) => (
                  <CommandItem key={n.key} value={n.label} className="justify-between font-mono text-[12px]">
                    {prefix}
                    {n.label}
                    {n.bad && <span className="font-sans text-[11px] text-destructive">{n.bad}</span>}
                  </CommandItem>
                ))}
              </CommandList>
            </Command>
          </PopoverContent>
        </Popover>
      )}
    </>
  )
}

// The phone bridge as the project's three keys see it (decision 0016): where a call to each number
// goes, what is wrong with each trunk and rule, and the phone calls we watched. Config comes from the
// SIP list APIs; calls come from the sip.* attributes of phone participants. Call outcomes and SIP codes
// need LiveKit's webhooks (Phase 2) or the Cloud telephony dashboard, which has no API.
// ?data=test swaps in a hand-written config to exercise many numbers and providers; it is labelled.

const tone = { high: "bg-destructive", medium: "bg-amber-500", low: "bg-muted-foreground/40" }

// A stat with a small picture of what the number means, not just the number.
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

function Chip({ children, bad }: { children: React.ReactNode; bad?: boolean }) {
  return <span className={cn("rounded-md px-1.5 py-0.5 text-[11px] whitespace-nowrap", bad ? "bg-destructive/10 text-destructive" : "bg-muted text-muted-foreground")}>{children}</span>
}

const clock = (ms: number) => new Date(ms).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
const secs = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s` : `${s.toFixed(1)} s`)

export function SipPage() {
  const test = new URLSearchParams(window.location.search).get("data") === "test"
  const read = useQuery({ ...sipQuery(), enabled: !test })
  if (test) return <SipView sip={synthetic as SipData} test />
  if (!read.data) return <QueryState icon={PhoneIncoming} what="the phone bridge" query={read} />
  return <SipView sip={read.data} test={false} />
}

function SipView({ sip, test }: { sip: SipData; test: boolean }) {
  const [tab, setTab] = useUrlState("tab", "paths")
  const calls = test ? [] : data.rooms.map((r, k) => ({ ...r, n: k + 1 })).filter((r) => r.participants.some((p) => p.kind === "sip"))
  const [openRoom, setOpenRoom] = useUrlPick<Room>("room", calls, (r) => r.sid)
  const hints = calls.flatMap((r) => r.participants.filter((p) => p.sip).map((p) => ({ trunkId: p.sip!.trunkId, twilio: Boolean((p.sip as { twilio?: boolean }).twilio) })))
  const rows = routes(sip, hints)
  const list = checks(sip, rows)
  const paths = inPaths(rows)
  const [q, setQ] = useState("")
  const findRef = useRef<HTMLInputElement>(null)
  // "/" jumps to the number search from anywhere on the page, as on Live.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (e.key === "/" && !(t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)))) {
        e.preventDefault()
        findRef.current?.focus()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])
  // Full numbers live in a gitignored local file; the committed fixture keeps only the last 4 digits.
  const [numbersFile, setNumbersFile] = useState<NumbersFile | null>(null)
  useEffect(() => {
    if (test) return
    fetch("/captures/sip-numbers.json")
      .then((r) => (r.ok ? r.json() : null))
      .then(setNumbersFile)
      .catch(() => setNumbersFile(null))
  }, [test])
  const full = (id: string, masked: string, dir: "in" | "out" | "lk") => {
    const fixture = dir === "in" ? sip.inbound.find((t) => t.id === id)?.numbers : dir === "out" ? sip.outbound.find((t) => t.id === id)?.numbers : sip.livekitNumbers.map((x) => x.number)
    const real = dir === "in" ? numbersFile?.inbound[id] : dir === "out" ? numbersFile?.outbound[id] : numbersFile?.livekit
    const i = fixture?.indexOf(masked) ?? -1
    return pretty((i >= 0 && real?.[i]) || masked)
  }
  const shown = (r: Route) => (r.trunk ? full(r.trunk.id, r.number, "in") : full("", r.number, "lk"))
  const digits = q.replace(/\D/g, "")
  const hit = (n: string) => digits.length > 0 && n.replace(/\D/g, "").includes(digits)
  const inShown = q ? paths.filter((p) => p.routes.some((r) => hit(shown(r)))) : paths
  const outShown = q ? sip.outbound.filter((t) => t.numbers.some((m) => hit(full(t.id, m, "out")))) : sip.outbound
  const issuesOf = (ids: (string | undefined)[]) => list.filter((c) => ids.some((id) => id && c.where.split(", ").includes(id)))

  const sev = (s: Check["severity"]) => list.filter((c) => c.severity === s).length
  const numbers = [...new Set(rows.filter((r) => !r.wildcard).map((r) => r.number))]
  // The phone legs LiveKit configures: media encryption per trunk, plus TLS for outbound signalling.
  const legs = [
    ...sip.inbound.map((t) => ({ name: `In ${t.id}`, label: enc(t.media.encryption), secure: enc(t.media.encryption) !== "RTP" })),
    ...(sip.outbound as Outbound[]).map((t) => ({ name: `Out ${t.id}`, label: `${transport(t.transport)} · ${enc(t.media.encryption)}`, secure: transport(t.transport) === "TLS" && enc(t.media.encryption) !== "RTP" })),
  ]
  // Median ring-to-answer over watched phone calls: INVITE time to the first read with callStatus active.
  const answers = calls
    .map((r) => r.participants.find((p) => p.kind === "sip")?.sip)
    .flatMap((x) => (x?.inviteMs && x.statuses.find((s) => s.status === "active") ? [x.statuses.find((s) => s.status === "active")!.t - x.inviteMs / 1000] : []))
    .sort((a, b) => a - b)
  const answer = answers.length ? answers[Math.floor(answers.length / 2)] : null
  const providers = [...new Set([...rows.map((r) => r.provider.name), ...sip.outbound.map((t) => providerOf(t.address) ?? t.address)])]

  return (
    <div className="mx-auto max-w-[1200px] space-y-12">
      {test && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-[13px] text-amber-800 dark:text-amber-300">
          <Flask size={16} /> Test data, not your project. A hand-written config used to check the layout with many numbers and providers.
        </div>
      )}
      <header className="space-y-6">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.02em]">Phone bridge</h1>
          {providers.map((p) => (
            <span key={p} className="rounded-full bg-muted px-2.5 py-0.5 text-[13px] text-muted-foreground">
              {p}
            </span>
          ))}
          <Tooltip>
            <TooltipTrigger asChild>
              <button type="button" aria-label="How this was read" className="text-muted-foreground hover:text-foreground">
                <Info size={18} />
              </button>
            </TooltipTrigger>
            <TooltipContent className="max-w-96 text-xs leading-relaxed">{`${sip.source} Read ${new Date(sip.readAt).toLocaleString()}.`}</TooltipContent>
          </Tooltip>
        </div>
        <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
          <StatCard icon={PhoneIncoming} value={numbers.length} label="Phone numbers">
            <div className="flex flex-wrap items-center gap-1">
              <NumberList small max={2} items={numbers.map((n) => ({ key: n, label: shown(rows.find((x) => x.number === n)!) }))} />
            </div>
          </StatCard>
          <StatCard icon={LockSimpleOpen} value={`${legs.filter((l) => l.secure).length} of ${legs.length}`} label="Phone legs encrypted" alert={legs.some((l) => !l.secure)}>
            <div className="flex gap-1">
              {legs.map((l) => (
                <Tooltip key={l.name}>
                  <TooltipTrigger asChild>
                    <span className={cn("h-2 flex-1 cursor-help rounded-full", l.secure ? "bg-primary" : "bg-destructive")} />
                  </TooltipTrigger>
                  <TooltipContent className="text-xs">{`${l.name}: ${l.label}`}</TooltipContent>
                </Tooltip>
              ))}
            </div>
            <p className="mt-1.5 text-[11px] text-muted-foreground">{`${legs.filter((l) => !l.secure).length} plain, ${legs.filter((l) => l.secure).length} encrypted`}</p>
          </StatCard>
          <StatCard icon={Timer} value={answer === null ? "Unknown" : `${answer.toFixed(1)} s`} label="Ring to answered">
            <p className="text-[11px] text-muted-foreground">
              {answer === null
                ? "No phone call watched yet"
                : `median of ${answers.length} watched ${answers.length === 1 ? "call" : "calls"}${calls.every((r) => r.participants.some((p) => p.sip?.trunkId && data.sipTrunks[p.sip.trunkId])) ? ", our test trunk only" : ""}`}
            </p>
          </StatCard>
          <StatCard icon={ShieldWarning} value={list.length} label="Checks to fix" alert={list.some((c) => c.severity === "high")}>
            <CategoryBar values={[sev("high"), sev("medium"), sev("low")]} colors={["alert", "amber", "quiet"]} showLabels={false} />
            <p className="mt-1.5 text-[11px] text-muted-foreground">{`${sev("high")} high · ${sev("medium")} medium · ${sev("low")} low`}</p>
          </StatCard>
        </div>
      </header>

      <Tabs value={tab} onValueChange={setTab} className="gap-5">
        <TabsList>
          <TabsTrigger value="paths">Call paths</TabsTrigger>
          <TabsTrigger value="checks">
            Checks <span className="ml-1 rounded-full bg-destructive/10 px-1.5 text-[11px] text-destructive tnum">{list.length}</span>
          </TabsTrigger>
          <TabsTrigger value="calls">
            Calls watched <span className="ml-1 text-[11px] text-muted-foreground tnum">{calls.length}</span>
          </TabsTrigger>
        </TabsList>
        <TabsContent value="paths">
          <section className="space-y-4">
            <div className="flex flex-wrap items-center justify-end gap-3">
              <div className="flex items-center gap-4">
                <PathLegend />
                <InputGroup className="h-8 w-64">
                  <InputGroupAddon>
                    <MagnifyingGlass />
                  </InputGroupAddon>
                  <InputGroupInput
                    ref={findRef}
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    onKeyDown={(e) => e.key === "Escape" && setQ("")}
                    inputMode="tel"
                    placeholder="Find a number"
                    aria-label="Find a number by any of its digits"
                  />
                  <InputGroupAddon align="inline-end">
                    {q ? (
                      <>
                        <InputGroupText className="text-[12px] tnum" aria-live="polite">
                          {digits ? `${inShown.length + outShown.length} ${inShown.length + outShown.length === 1 ? "path" : "paths"}` : "digits only"}
                        </InputGroupText>
                        <InputGroupButton size="icon-xs" onClick={() => (setQ(""), findRef.current?.focus())} aria-label="Clear search">
                          <X />
                        </InputGroupButton>
                      </>
                    ) : (
                      <Kbd>/</Kbd>
                    )}
                  </InputGroupAddon>
                </InputGroup>
              </div>
            </div>
            {inShown.map((p) => {
              const nums = p.routes.filter((r) => !r.wildcard)
              const n = issuesOf([p.trunk?.id, ...nums.map((r) => r.number), ...p.rules.map((x) => x.id)]).length
              return (
                <div key={p.key} className="rounded-2xl bg-muted/40 p-5">
                  <div className="flex flex-wrap items-center gap-2">
                    <PhoneIncoming size={16} className="text-muted-foreground" />
                    <span className="text-[13px] font-medium">Inbound</span>
                    {p.wildcard && <Chip>any number</Chip>}
                    <NumberList items={nums.map((r) => ({ key: r.key, label: shown(r), hint: `${r.provider.name}. ${r.provider.how}`, bad: r.duplicate ? "2 trunks" : undefined, match: Boolean(q) && hit(shown(r)) }))} />
                    <span className="ml-auto text-[12px] tnum">
                      {n ? (
                        <span className="text-destructive">
                          {n} {n === 1 ? "issue" : "issues"}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">no issues</span>
                      )}
                    </span>
                  </div>
                  <PathDiagram path={inboundPath(p, list)} />
                </div>
              )
            })}
            {outShown.map((t) => {
              const n = issuesOf([t.id]).length
              return (
                <div key={t.id} className="rounded-2xl bg-muted/40 p-5">
                  <div className="flex flex-wrap items-center gap-2">
                    <PhoneOutgoing size={16} className="text-muted-foreground" />
                    <span className="text-[13px] font-medium">Outbound</span>
                    <NumberList prefix="from " items={t.numbers.map((m) => ({ key: m, label: full(t.id, m, "out"), match: Boolean(q) && hit(full(t.id, m, "out")) }))} />
                    <span className="ml-auto text-[12px] tnum">
                      {n ? (
                        <span className="text-destructive">
                          {n} {n === 1 ? "issue" : "issues"}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">no issues</span>
                      )}
                    </span>
                  </div>
                  <PathDiagram path={outboundPath(t, list)} />
                </div>
              )
            })}
            {q && !inShown.length && !outShown.length && <p className="rounded-2xl border border-dashed px-4 py-6 text-center text-[13px] text-muted-foreground">No path carries a number matching "{q}".</p>}
          </section>
        </TabsContent>
        <TabsContent value="checks">
          <section className="space-y-3">
            <ul className="divide-y rounded-xl border">
              {list.map((c, k) => (
                <li key={k}>
                  <Collapsible>
                    <CollapsibleTrigger className="group flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/50">
                      <span className={cn("size-2 shrink-0 rounded-full", tone[c.severity])} />
                      <span className="flex-1 text-[14px]">{c.title}</span>
                      <span className="font-mono text-[11px] text-muted-foreground">{c.where}</span>
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
          </section>
        </TabsContent>
        <TabsContent value="calls">
          <section className="space-y-3">
            <div className="flex flex-wrap items-baseline justify-end gap-3">
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="cursor-help text-[12px] text-muted-foreground">Outcomes and SIP codes: Phase 2</span>
                </TooltipTrigger>
                <TooltipContent className="max-w-72 text-xs">
                  Why a call ended and its SIP response code come from LiveKit's webhooks (disconnect_reason) once the VoiceGateway server runs. The Cloud telephony call log has no API.
                </TooltipContent>
              </Tooltip>
            </div>
            <ul className="divide-y rounded-xl border">
              {calls.map((r) => {
                const p = r.participants.find((x) => x.kind === "sip")!
                const agent = r.participants.find((x) => x.kind === "agent")
                const t0 = p.sip?.inviteMs ?? r.createdMs
                const answered = p.sip?.statuses.find((s) => s.status === "active")
                const trunk = p.sip?.trunkId ? data.sipTrunks[p.sip.trunkId] : undefined
                const marks = [{ at: r.createdMs, label: "room" }, ...(agent ? [{ at: agent.joinedAtMs, label: "agent" }] : []), ...(answered ? [{ at: answered.t * 1000, label: "answered" }] : [])]
                const end = Math.max(...marks.map((m) => m.at)) - t0 || 1
                return (
                  <li key={r.sid}>
                    <button type="button" onClick={() => setOpenRoom(r)} className="grid w-full grid-cols-[72px_minmax(0,1fr)_minmax(0,2fr)_80px] items-center gap-4 px-4 py-3 text-left hover:bg-muted/50">
                      <span className="text-[13px] tnum">Room {r.n}</span>
                      <span className="text-[13px]">
                        <span className="flex items-center gap-1.5">
                          {p.sip?.ruleId ? <PhoneIncoming size={14} /> : <PhoneOutgoing size={14} />}
                          {clock(t0)}
                        </span>
                        <span className="text-[11px] text-muted-foreground">via {trunk ? `${trunk.name} (deleted)` : (p.sip?.trunkId ?? "unknown trunk")}</span>
                      </span>
                      <span className="relative h-6">
                        <span className="absolute inset-x-0 top-1/2 h-px bg-border" />
                        {marks.map((m) => (
                          <Tooltip key={m.label}>
                            <TooltipTrigger asChild>
                              <span className="absolute top-1/2 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center" style={{ left: `${((m.at - t0) / end) * 100}%` }}>
                                <span className="size-2.5 rounded-full bg-primary" />
                              </span>
                            </TooltipTrigger>
                            <TooltipContent>{`${m.label} at +${((m.at - t0) / 1000).toFixed(2)} s after the INVITE`}</TooltipContent>
                          </Tooltip>
                        ))}
                        <span className="absolute top-1/2 left-0 size-2.5 -translate-y-1/2 rounded-full bg-foreground" />
                        <span className="absolute -bottom-2 right-0 text-[11px] text-muted-foreground tnum">{answered ? `answered in ${((answered.t * 1000 - t0) / 1000).toFixed(1)} s` : "not answered while watched"}</span>
                      </span>
                      <span className="text-right text-[13px] tnum">{secs(p.lastSeen - t0 / 1000)}</span>
                    </button>
                  </li>
                )
              })}
              {calls.length === 0 && <li className="px-4 py-6 text-center text-[13px] text-muted-foreground">No phone calls while we watched.</li>}
            </ul>
          </section>
        </TabsContent>
      </Tabs>

      <RoomDrawer room={openRoom} open={openRoom !== null} onOpenChange={(o) => !o && setOpenRoom(null)} />
    </div>
  )
}
