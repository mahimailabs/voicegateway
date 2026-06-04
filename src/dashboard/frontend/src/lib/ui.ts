export type Accent = 'yellow' | 'blue' | 'green' | 'pink' | 'orange';

export const ACCENT_COLORS: Record<Accent, string> = {
  yellow: '#FFD166',
  blue: '#118AB2',
  green: '#8AC926',
  pink: '#FF006E',
  orange: '#FB8500',
};

export function formatCost(value: number | undefined | null, decimals = 2): string {
  if (value == null) return '$0.00';
  return `$${value.toFixed(decimals)}`;
}

export function formatMs(value: number | undefined | null): string {
  if (value == null || value === 0) return '-';
  return `${Math.round(value)}ms`;
}

export function latencyBadgeClass(ms: number | undefined | null): string {
  if (ms == null || ms === 0) return 'neo-badge--black';
  if (ms < 200) return 'neo-badge--online';
  if (ms < 500) return 'neo-badge--yellow';
  return 'neo-badge--offline';
}

export function statusBadgeClass(status: string | undefined): string {
  if (!status) return 'neo-badge--black';
  if (status === 'success') return 'neo-badge--online';
  if (status === 'fallback') return 'neo-badge--yellow';
  return 'neo-badge--offline';
}

// ---------------------------------------------------------------------------
// Phase 2 fleet: agent telemetry-recency status (active / idle / dormant).
// ---------------------------------------------------------------------------

export type AgentStatus = 'active' | 'idle' | 'dormant';

/** Telemetry-recency status from the agent's last-seen epoch seconds. */
export function agentStatus(lastSeen: number | null | undefined): AgentStatus {
  if (lastSeen == null) return 'dormant';
  const ageSec = Date.now() / 1000 - lastSeen;
  if (ageSec < 300) return 'active'; // < 5 min
  if (ageSec < 3600) return 'idle'; // < 1 h
  return 'dormant';
}

export function agentStatusBadgeClass(status: AgentStatus): string {
  if (status === 'active') return 'neo-badge--online';
  if (status === 'idle') return 'neo-badge--yellow';
  return 'neo-badge--offline';
}

/** Compact "Ns / Nm / Nh / Nd ago" from an epoch-seconds timestamp. */
export function formatRelativeTime(lastSeen: number | null | undefined): string {
  if (lastSeen == null) return '—';
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - lastSeen));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}
