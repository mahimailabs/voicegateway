import { JOIN_MS } from "@/lib/thresholds"
import raw from "@/mocks/agents.json"
import { type Room, data as rooms } from "../media-server/room-drawer"

// The agents of a project, from two reads: LiveKit Cloud's CloudAgent API for deployed agents, and the
// jobs and agent participants in the rooms we watched for every agent, including self-hosted workers
// that no API lists.

type Deployment = {
  deployment: string | null
  region: string | null
  serverRegion: string | null
  status: string | null
  replicas: number | null
  minReplicas: number | null
  maxReplicas: number | null
  curCpu: string | null
  cpuReq: string | null
  cpuLimit: string | null
  curMem: string | null
  memReq: string | null
  memLimit: string | null
  lastScrapedAt: string | null
  version: string | null
}
type CloudAgent = { id: string; name: string | null; version: string | null; deployedAt: string | null; deployments: Deployment[]; versions: { version: string; current: boolean | null; draining: boolean | null; active: boolean | null; status: string | null; createdAt: string | null; deployedAt: string | null }[] }
const cloud = raw as unknown as { readAt: string; source: string; agents: CloudAgent[] }
export const source = cloud.source
export const readAt = cloud.readAt

// Kubernetes quantities as the API returns them: "2" cores, "500m" millicores, "1Gi", "4Gi", "512Mi".
export const cores = (q: string | null) => (q == null ? null : q.endsWith("m") ? Number(q.slice(0, -1)) / 1000 : Number(q))
export const gib = (q: string | null) => {
  if (q == null) return null
  const m = q.match(/^([\d.]+)(Ki|Mi|Gi|Ti)?$/)
  if (!m) return null
  return Number(m[1]) / ({ Ki: 1024 ** 2, Mi: 1024, Gi: 1, Ti: 1 / 1024 }[m[2] as "Ki"] ?? 1024 ** 3)
}

export type Job = { room: Room; jobId: string; workerId: string | null; status: string; dispatchedMs: number; joinedMs: number | null; failed: boolean; region: string | null }
export type Agent = {
  key: string
  name: string | null
  cloud: (CloudAgent & { deployment: Deployment | null }) | null
  jobs: Job[]
  regions: string[]
  workers: string[]
}

const realVersion = (v: string | null) => (v && v !== "---" ? v : null)

export function agents(): Agent[] {
  const all: Room[] = rooms.rooms.map((r, k) => ({ ...r, n: k + 1 }))
  const byName = new Map<string, Agent>()
  for (const c of cloud.agents) {
    const key = c.name ?? c.id
    byName.set(key, { key, name: c.name, cloud: { ...c, version: realVersion(c.version), deployment: c.deployments.find((d) => !d.deployment || d.deployment === "production") ?? c.deployments[0] ?? null }, jobs: [], regions: [], workers: [] })
  }
  for (const r of all) {
    const agentsIn = r.participants.filter((p) => p.kind === "agent")
    for (const name of [...new Set(agentsIn.map((p) => p.agentName ?? "unnamed"))]) {
      const a = byName.get(name) ?? { key: name, name, cloud: null, jobs: [], regions: [], workers: [] }
      byName.set(name, a)
      const ps = agentsIn.filter((p) => (p.agentName ?? "unnamed") === name)
      for (const p of ps) if (p.region && !a.regions.includes(p.region)) a.regions.push(p.region)
      const jobs = (r.dispatches ?? []).filter((d) => d.agentName === name).flatMap((d) => d.jobs.map((j) => ({ d, j })))
      if (jobs.length)
        for (const { d, j } of jobs) {
          if (j.workerId && !a.workers.includes(j.workerId)) a.workers.push(j.workerId)
          a.jobs.push({ room: r, jobId: j.id, workerId: j.workerId, status: j.status, dispatchedMs: d.createdAtMs ?? NaN, joinedMs: ps[0]?.joinedAtMs ?? null, failed: j.statuses.some((s) => s.status === "JS_FAILED"), region: ps[0]?.region ?? null })
        }
      else
        // The earliest watch kept no dispatch detail: one job per agent participant, timing unknown.
        a.jobs.push({ room: r, jobId: "", workerId: null, status: "unknown", dispatchedMs: NaN, joinedMs: ps[0]?.joinedAtMs ?? null, failed: false, region: ps[0]?.region ?? null })
    }
  }
  return [...byName.values()].sort((a, b) => Number(Boolean(b.cloud)) - Number(Boolean(a.cloud)) || b.jobs.length - a.jobs.length)
}

// Dispatch to the agent's first join: how long a caller waits for the agent to arrive after LiveKit
// assigns the job. Only jobs with dispatch detail count.
export const joinDelays = (js: Job[]) => js.filter((j) => Number.isFinite(j.dispatchedMs) && j.joinedMs !== null).map((j) => (j.joinedMs! - j.dispatchedMs) / 1000)
export const median = (xs: number[]) => (xs.length ? [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)] : null)

// Agents we ran ourselves to capture test calls (scratchpad capture/agent.py), not the project's own.
export const TEST_AGENTS = new Set(["vg-capture"])

export type Check = { severity: "high" | "medium" | "low"; title: string; where: string; why: string; fix: string; doc: string }
const docs = "https://docs.livekit.io"
export function checks(list: Agent[]): Check[] {
  const out: Check[] = []
  for (const a of list) {
    const d = a.cloud?.deployment
    const status = d?.status ?? null
    if (a.cloud && status && ["Error", "CrashLoop", "Disabled", "Build Failed", "Server Error", "Suspended"].includes(status))
      out.push({ severity: "high", title: `Cloud agent is in ${status}`, where: a.cloud.id, why: "LiveKit Cloud reports the agent in an error state, so dispatches to it fail.", fix: "Check the agent's logs and deploy a fixed version.", doc: `${docs}/reference/developer-tools/livekit-cli/agent/` })
    if (a.cloud && status === "Setting Up" && !a.cloud.version)
      out.push({ severity: "low", title: "Cloud agent never deployed", where: a.cloud.id, why: "The agent exists but has no name and no version and is still Setting Up. It takes no calls and holds a slot.", fix: "Deploy it, or delete it with lk agent delete.", doc: `${docs}/deploy/agents/managing-deployments/` })
    if (a.cloud && status === "Sleeping" && (!d?.deployment || d.deployment === "production"))
      out.push({ severity: "medium", title: "Production agent is asleep", where: a.cloud.id, why: "Production scaled to zero. The next caller waits for a cold start, which the docs put at 10 to 20 seconds, before the agent joins. Production stays warm on paid plans.", fix: "Move the project to a paid plan, or keep traffic flowing to it.", doc: `${docs}/deploy/agents/managing-deployments/` })
    const failed = a.jobs.filter((j) => j.failed)
    const test = TEST_AGENTS.has(a.name ?? "")
    if (failed.length)
      out.push({ severity: "high", title: `${test ? "Test data: " : ""}${failed.length} ${failed.length === 1 ? "job" : "jobs"} failed and LiveKit retried`, where: a.name ?? a.key, why: `${test ? "These jobs ran on VoiceGateway's own capture agent during our test calls, not on this project's agents. " : ""}ListDispatch reported ${failed.map((j) => j.jobId).join(", ")} as failed. LiveKit reassigns a failed job after about 15 seconds, so the caller waits in a room with no working agent.`, fix: "Read the worker's logs for the crash; the reason is not in the API.", doc: `${docs}/agents/server/lifecycle/` })
    const delay = median(joinDelays(a.jobs))
    if (delay !== null && delay * 1000 > JOIN_MS)
      out.push({ severity: "medium", title: `Agent joins ${delay.toFixed(1)} s after dispatch, over the ${JOIN_MS / 1000} s threshold`, where: a.name ?? a.key, why: "LiveKit dispatches in under 150 ms; the rest is the worker starting a process and connecting. The caller hears nothing meanwhile.", fix: "Keep idle processes warm (num_idle_processes) and move slow setup into prewarm.", doc: `${docs}/agents/server/options/` })
    if (!a.cloud && a.jobs.length)
      out.push({ severity: "low", title: "Capacity unknown for an agent not deployed here", where: a.name ?? a.key, why: "This agent is not a LiveKit Cloud deployment in this project, and no API lists other workers or their load. We see it only through the jobs it took.", fix: "Expose the worker's metrics endpoint or run the VoiceGateway SDK (Phase 2).", doc: `${docs}/agents/server/options/` })
  }
  const rank = { high: 0, medium: 1, low: 2 }
  return out.sort((a, b) => rank[a.severity] - rank[b.severity])
}
