import type { Egress, Ingress, Sip } from "@/lib/api-types"
import egressRaw from "@/mocks/egress.json"
import ingressRaw from "@/mocks/ingress.json"
import sipRaw from "@/mocks/sip.json"

// The LiveKit reads the mock API serves, adapted from the captured fixtures to the contract
// (docs/api/openapi.yaml). Captured times are Unix seconds; the contract carries RFC 3339 UTC. Fields
// the capture kept that the contract does not carry are left out, never renamed into something else.

// Unix seconds to RFC 3339, keeping an unreported time unknown.
const iso = (s: number | null | undefined) => (typeof s === "number" ? new Date(s * 1000).toISOString() : null)

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
