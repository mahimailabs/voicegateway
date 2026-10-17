import { DownloadSimple as ArrowDownToLine, UploadSimple as ArrowUpFromLine, Brain, Cloud, Eye, HardDrives, Microphone, Phone, Robot, SpeakerHigh } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { useQuery } from "@tanstack/react-query"
import { QueryState } from "@/components/page-state"
import { snapshotQuery } from "@/lib/api"
import type { ProjectSnapshot } from "@/lib/api-types"

// The project as LiveKit sees it, read from its URL, key and secret (decision 0016): the media server,
// the phone bridge, the agents, their models, then egress and ingress. Each branch says where its data
// came from and what the keys cannot reach.

const regionName: Record<string, string> = { oashburn1b: "Ashburn, Virginia", osanjose1a: "San Jose, California", otoronto1a: "Toronto" }
const place = (r: string | null) => (r ? `${regionName[r] ?? r}` : "region unknown")

function Branch({ icon: I, title, meta, children, last }: { icon: Icon; title: string; meta?: React.ReactNode; children?: React.ReactNode; last?: boolean }) {
  return (
    <li className="relative pl-10">
      {/* connector into the trunk line */}
      <span className="absolute top-0 left-[15px] w-px bg-border" style={{ height: last ? 22 : "100%" }} />
      <span className="absolute top-[22px] left-[15px] h-px w-4 bg-border" />
      <div className="pb-6">
        <div className="flex items-center gap-3">
          <span className="flex size-8 items-center justify-center rounded-lg border bg-background"><I size={16} /></span>
          <h2 className="text-[15px] font-semibold">{title}</h2>
          {meta && <span className="text-[13px] text-muted-foreground">{meta}</span>}
        </div>
        {children && <div className="mt-3 ml-11">{children}</div>}
      </div>
    </li>
  )
}

function Rows({ children }: { children: React.ReactNode }) {
  return <ul className="divide-y rounded-xl border bg-background">{children}</ul>
}
function Row({ title, sub, right, muted }: { title: React.ReactNode; sub?: React.ReactNode; right?: React.ReactNode; muted?: boolean }) {
  return (
    <li className="flex items-center gap-4 px-4 py-3">
      <div className="min-w-0 flex-1">
        <p className={cn("text-sm", muted ? "text-muted-foreground" : "font-medium")}>{title}</p>
        {sub && <p className="truncate text-xs text-muted-foreground">{sub}</p>}
      </div>
      {right}
    </li>
  )
}

const roleIcon: Record<string, Icon> = { stt: Microphone, llm: Brain, tts: SpeakerHigh }
const roleName: Record<string, string> = { stt: "Speech to text", llm: "LLM", tts: "Text to speech" }

const optionName: Record<string, string> = {
  endpointing: "Endpointing",
  interruption: "Interruption",
  preemptiveGeneration: "Preemptive generation",
  maxToolSteps: "Max tool steps",
  userAwayTimeout: "User away timeout",
  ivrDetection: "IVR detection",
}

export function ProjectPage() {
  const read = useQuery(snapshotQuery())
  if (!read.data) return <QueryState icon={Cloud} what="the project" query={read} />
  return <ProjectView snap={read.data} />
}

function ProjectView({ snap }: { snap: ProjectSnapshot }) {
  const host = snap.url.replace(/^wss?:\/\//, "")
  const cloud = host.endsWith(".livekit.cloud")
  const agentsInRooms = snap.rooms.flatMap((r) => r.participants.filter((p) => p.kind === "agent").map((p) => ({ ...p, room: r.name, job: r.dispatches.flatMap((d) => d.jobs).find((j) => p.identity.endsWith(j.id)) })))
  const ruleAgents = snap.sip.rules.flatMap((r) => r.agents.map((a) => ({ name: a, rule: r.name })))
  const callers = snap.rooms.flatMap((r) => r.participants.filter((p) => p.kind !== "agent"))
  const obs = snap.observed

  return (
    <div className="mx-auto max-w-[960px] space-y-8">
      <div className="space-y-1.5">
        <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.02em] text-balance">
          {host}. <span className="text-muted-foreground">Everything this project's three keys can reach.</span>
        </h1>
        <p className="text-[13px] text-muted-foreground">
          Read {new Date(snap.readAt).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })} with the URL, API key and secret only. Phone numbers masked; client names withheld.
        </p>
      </div>

      <ul className="relative">
        <li className="relative pb-6">
          <div className="flex items-center gap-3">
            <span className="flex size-8 items-center justify-center rounded-lg bg-foreground text-background">{cloud ? <Cloud size={16} weight="fill" /> : <HardDrives size={16} />}</span>
            <div>
              <p className="text-[15px] font-semibold">{cloud ? "LiveKit Cloud project" : "Self-hosted LiveKit server"}</p>
              <p className="font-mono text-xs text-muted-foreground">{snap.url}</p>
            </div>
          </div>
        </li>

        <Branch icon={HardDrives} title="Media server" meta={`${snap.rooms.length} live ${snap.rooms.length === 1 ? "room" : "rooms"}`}>
          <Rows>
            {snap.rooms.map((r) => (
              <Row key={r.sid} title={<span className="font-mono text-[13px]">{r.name}</span>} sub={`${r.participants.length} participants · ${r.createdAt === null ? "opening time unknown" : `opened ${new Date(r.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}`} />
            ))}
            {callers.map((p) => (
              <Row key={p.identity} muted title={`Caller · ${p.kind === "sip" ? "phone" : "web or app"}`} sub={`${place(p.region)} · ${p.tracks.some((t) => (t.audioFeatures as string[]).includes("TF_ENHANCED_NOISE_CANCELLATION")) ? "noise cancellation on their mic" : "no noise cancellation reported on their mic"}`} />
            ))}
          </Rows>
        </Branch>

        <Branch icon={Phone} title="Phone bridge (SIP)" meta={`${snap.sip.inbound.length} inbound, ${snap.sip.outbound.length} outbound, ${snap.sip.rules.length} dispatch ${snap.sip.rules.length === 1 ? "rule" : "rules"}`}>
          <Rows>
            {snap.sip.inbound.map((t) => (
              <Row key={t.id} title={t.name} sub={`Inbound · ${t.numbers.join(", ")}`} right={<Badge variant="outline" className="font-normal">{t.krispEnabled ? "Krisp noise cancellation on" : "Krisp off"}</Badge>} />
            ))}
            {snap.sip.outbound.map((t) => (
              <Row key={t.id} title={t.name} sub={`Outbound via ${t.address} · ${t.numbers.join(", ")}`} />
            ))}
            {snap.sip.rules.map((r) => (
              <Row key={r.id} title={r.name} sub={`Dispatch rule · ${r.type === "dispatch_rule_individual" ? "a new room per caller" : r.type} · sends the ${r.agents.join(", ")}`} />
            ))}
          </Rows>
        </Branch>

        <Branch icon={Robot} title="Agents" meta={`${agentsInRooms.length} in a live room, ${ruleAgents.length} named by a dispatch rule, 1 observed`}>
          <Rows>
            {agentsInRooms.map((a) => (
              <Row
                key={a.identity}
                title={a.agentName ?? "Unnamed agent"}
                sub={`In a live room · ${place(a.region)}${a.region?.startsWith("o") ? " (LiveKit Cloud region)" : ""} · job ${a.job?.id ?? "unknown"} on worker ${a.job?.workerId ?? "unknown"} · ${a.agentState ?? "state unknown"}`}
                right={
                  <Tooltip>
                    <TooltipTrigger asChild><span className="cursor-help text-xs text-muted-foreground underline decoration-dotted underline-offset-4">models not observed</span></TooltipTrigger>
                    <TooltipContent className="max-w-72">This room has a visitor in it. Observing a live room is a per-project setting, off by default (decision 0016), so its models were not read.</TooltipContent>
                  </Tooltip>
                }
              />
            ))}
            {ruleAgents.map((a) => (
              <Row key={a.name} muted title={a.name} sub={`Named by the dispatch rule "${a.rule}" · not in a room right now, so nothing more is visible until a call comes in`} />
            ))}
            {obs ? (
              <Row title={obs.agentName} sub={`Observed in a test room · ${obs.framework.sdk} ${obs.framework.version} · agent "${obs.agentId}" · tools: ${obs.tools.join(", ")}`} right={<Badge variant="outline" className="gap-1 font-normal"><Eye size={12} />observed</Badge>} />
            ) : (
              <Row muted title="No agent observed" sub="No agent was watched in a room at this read, so its framework, models and options are not known." />
            )}
          </Rows>
        </Branch>

        {obs ? (
        <Branch icon={Brain} title="Models" meta={`of ${obs.agentName}, as it reported them`}>
          <div className="space-y-3">
            <Rows>
              {(["stt", "llm", "tts"] as const).map((role) => {
                const m = obs.models.find((x) => x.role === role)
                const I = roleIcon[role]
                return (
                  <li key={role} className="flex items-center gap-3 px-4 py-3">
                    <I size={18} className="text-muted-foreground" />
                    <p className="flex-1 text-sm">{roleName[role]}</p>
                    {m ? <p className="text-sm font-medium">{m.provider} · {m.model}</p> : <p className="text-sm text-muted-foreground">not used yet when observed</p>}
                  </li>
                )
              })}
            </Rows>
            <dl className="grid gap-x-8 gap-y-3 rounded-xl border bg-background p-4 text-sm sm:grid-cols-2">
              {Object.entries(obs.options).map(([k, v]) => (
                <div key={k}>
                  <dt className="text-xs text-muted-foreground">{optionName[k] ?? k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
            <p className="text-xs leading-relaxed text-muted-foreground">
              Not readable from LiveKit with the keys: the VAD model, the turn-detector model and noise cancellation inside the agent. They arrive with the agent's own upload (decision 0014).
            </p>
          </div>
        </Branch>
        ) : (
          <Branch icon={Brain} title="Models" meta="no agent observed" />
        )}

        <Branch icon={ArrowUpFromLine} title="Egress" meta={snap.egress === 0 ? "none" : `${snap.egress}`} />
        <Branch icon={ArrowDownToLine} title="Ingress" meta={snap.ingress === 0 ? "none" : `${snap.ingress}`} last />
      </ul>
    </div>
  )
}
