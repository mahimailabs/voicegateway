import type { CapacityRun, CloudAgents, Egress, Ingress, MediaServer, ProjectSnapshot, ProviderProbes, Sip, Workers } from "@/lib/api-types"
import egressRaw from "@/mocks/egress.json"
import ingressRaw from "@/mocks/ingress.json"
import sipRaw from "@/mocks/sip.json"
import mediaRaw from "@/mocks/media-server.json"
import agentsRaw from "@/mocks/agents.json"
import capacityRaw from "@/mocks/capacity.json"
import snapshotRaw from "@/mocks/project-snapshot.json"
import workersRaw from "@/mocks/workers.json"
import probesRaw from "@/mocks/network-probe.json"
import { recordingId } from "./recordings"

// The LiveKit reads the mock API serves, adapted from the captured fixtures to the contract
// (docs/api/openapi.yaml). Captured times are Unix seconds; the contract carries RFC 3339 UTC. Fields
// the capture kept that the contract does not carry are left out, never renamed into something else.

// Unix seconds, or milliseconds, to RFC 3339, keeping an unreported time unknown.
const iso = (s: number | null | undefined) => (typeof s === "number" ? new Date(s * 1000).toISOString() : null)
const isoMs = (ms: number | null | undefined) => (typeof ms === "number" ? new Date(ms).toISOString() : null)

type Captured<T> = Record<string, unknown> & T

export function egress(): Egress {
  const d = egressRaw as unknown as { source: string; egress: Captured<{ startedAt: number | null; endedAt: number | null; history: { t: number; status: string }[]; request: Captured<object> }>[]; rejected: { t: number; case: string; error: string }[] }
  return {
    source: d.source,
    egress: d.egress.map(({ roomSid: _r, sourceType: _s, backupStorageUsed: _b, streams: _st, startedAt, endedAt, history, request, ...e }) => {
      const { layout: _l, customBaseUrl: _c, ...req } = request
      return { ...e, startedAt: iso(startedAt), endedAt: iso(endedAt), history: history.map((h) => ({ at: iso(h.t), status: h.status })), request: req } as Egress["egress"][number]
    }),
    rejected: d.rejected.map((r) => ({ at: iso(r.t), case: r.case, error: r.error })),
  }
}

export function ingress(): Ingress {
  const d = ingressRaw as unknown as { source: string; ingress: Captured<{ startedAt: number | null; endedAt: number | null; createdAt: number | null; history: { t: number; status: string }[] }>[] }
  return {
    source: d.source,
    ingress: d.ingress.map(({ roomSid: _r, startedAt, endedAt, createdAt, history, ...i }) => ({
      ...i,
      startedAt: iso(startedAt),
      endedAt: iso(endedAt),
      createdAt: iso(createdAt),
      history: history.map((h) => ({ at: iso(h.t), status: h.status })),
    })) as Ingress["ingress"],
  }
}

export function sip(): Sip {
  const d = sipRaw as unknown as Sip & { inbound: Captured<object>[]; outbound: Captured<object>[]; rules: Captured<object>[] }
  const strip = <T extends object>(x: Captured<T>) => {
    const { createdAt: _c, updatedAt: _u, ringingTimeout: _rt, maxCallDuration: _m, headerMappings: _h, ...rest } = x
    return rest
  }
  return { ...d, inbound: d.inbound.map(strip), outbound: d.outbound.map(strip), rules: d.rules.map(strip) } as Sip
}

// The media server read. The capture keeps some times in milliseconds (createdMs, joinedAtMs,
// createdAtMs, startedAtMs, versionMs, inviteMs) and the rest in seconds; both become RFC 3339. A
// recording's id is its file name, as for calls (recordings.ts).
export function mediaServer(): MediaServer {
  type At = { t: number; [k: string]: unknown }
  type R = Record<string, unknown> & {
    createdMs: number | null
    firstSeen: number | null
    lastSeen: number | null
    participants: (Record<string, unknown> & { joinedAtMs: number | null; firstSeen: number | null; lastSeen: number | null; states: At[]; tracks: (Record<string, unknown> & { versionMs: number | null })[] | null; sip?: Record<string, unknown> & { inviteMs: number | null; statuses: At[] } })[]
    dispatches: (Record<string, unknown> & { createdAtMs: number | null; jobs: (Record<string, unknown> & { startedAtMs: number | null; statuses: At[] })[] })[] | null
    recording: (Record<string, unknown> & { url: string }) | null
  }
  const d = mediaRaw as unknown as Record<string, unknown> & { windows: (Record<string, unknown> & { start: number | null; end: number | null })[]; rooms: R[] }
  const at = ({ t, ...x }: At) => ({ ...x, at: iso(t) })
  return {
    ...d,
    windows: d.windows.map((w) => ({ ...w, start: iso(w.start), end: iso(w.end) })),
    rooms: d.rooms.map(({ createdMs, firstSeen, lastSeen, participants, dispatches, recording, ...r }) => ({
      ...r,
      createdAt: isoMs(createdMs),
      firstSeenAt: iso(firstSeen),
      lastSeenAt: iso(lastSeen),
      participants: participants.map(({ joinedAtMs, firstSeen: pf, lastSeen: pl, states, tracks, sip, ...p }) => ({
        ...p,
        joinedAt: isoMs(joinedAtMs),
        firstSeenAt: iso(pf),
        lastSeenAt: iso(pl),
        states: states.map(at),
        tracks: tracks && tracks.map(({ versionMs, ...t }) => ({ ...t, versionAt: isoMs(versionMs) })),
        ...(sip ? { sip: (({ inviteMs, statuses, ...x }) => ({ ...x, invitedAt: isoMs(inviteMs), statuses: statuses.map(at) }))(sip) } : {}),
      })),
      dispatches: dispatches && dispatches.map(({ createdAtMs, jobs, ...x }) => ({ ...x, createdAt: isoMs(createdAtMs), jobs: jobs.map(({ startedAtMs, statuses, ...j }) => ({ ...j, startedAt: isoMs(startedAtMs), statuses: statuses.map(at) })) })),
      recording: recording && { ...recording, id: recordingId(recording.url) },
    })),
  } as MediaServer
}

// The CloudAgent API read. The capture also kept each deployment's enabled flag and event log, which
// the contract does not carry.
export function cloudAgents(): CloudAgents {
  const d = agentsRaw as unknown as Omit<CloudAgents, "agents"> & { agents: (Record<string, unknown> & { deployments: Record<string, unknown>[] })[] }
  return {
    ...d,
    agents: d.agents.map((a) => ({ ...a, deployments: a.deployments.map(({ deploymentEnabled: _e, events: _v, ...x }) => x) })),
  } as CloudAgents
}

// The one capacity run we have, with the capture's snake_case step fields in the contract's names.
export function capacity(): CapacityRun {
  type Step = { step: number; joined: number; pending: number; join_s: number[]; answer_s: number[]; host_load_1m: number[]; host_cores: number }
  const d = capacityRaw as unknown as Omit<CapacityRun, "steps"> & { steps: Step[] }
  return {
    ...d,
    steps: d.steps.map(({ join_s, answer_s, host_load_1m, host_cores, ...x }) => ({ ...x, joinSeconds: join_s, answerSeconds: answer_s, hostLoad1m: host_load_1m, hostCores: host_cores })),
  } as CapacityRun
}

// The project snapshot: what the three keys see at one read. Room and join times in seconds become
// RFC 3339, and the observed agent's options take the contract's camelCase names.
export function projectSnapshot(): ProjectSnapshot {
  type Raw = Omit<ProjectSnapshot, "rooms" | "observed"> & {
    rooms: (Record<string, unknown> & { createdAt: number | null; participants: (Record<string, unknown> & { joinedAt: number | null })[] })[]
    observed: Record<string, unknown> & { options: { endpointing: string; interruption: string; preemptive_generation: string; max_tool_steps: string; user_away_timeout: string; ivr_detection: string } }
  }
  const d = snapshotRaw as unknown as Raw
  const { preemptive_generation, max_tool_steps, user_away_timeout, ivr_detection, ...options } = d.observed.options
  return {
    ...d,
    rooms: d.rooms.map((r) => ({ ...r, createdAt: iso(r.createdAt), participants: r.participants.map((p) => ({ ...p, joinedAt: iso(p.joinedAt) })) })),
    observed: { ...d.observed, options: { ...options, preemptiveGeneration: preemptive_generation, maxToolSteps: max_tool_steps, userAwayTimeout: user_away_timeout, ivrDetection: ivr_detection } },
  } as ProjectSnapshot
}

// Worker metric samples and the host-to-provider probe: the captures already carry the contract's shape.
export const workers = (): Workers => workersRaw as Workers
export const providerProbes = (): ProviderProbes => probesRaw as ProviderProbes
