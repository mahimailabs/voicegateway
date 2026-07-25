import type { AgentRow } from '../lib/types';
import { formatBytes } from '../lib/ui';

// A compact CPU + memory readout for an agent. Percentages are shares of the
// machine's capacity (utilized); the track behind each bar is what's left. Every
// number is a live heartbeat sample; a modality with no sample says so rather
// than showing a zero. Used behind a toggle on the Overview card and inline on
// the Agents page.

function barColor(pct: number): string {
  if (pct >= 85) return 'var(--vg-red)';
  if (pct >= 60) return 'var(--vg-amber)';
  return 'var(--vg-teal)';
}

function Meter({
  label,
  pct,
  right,
}: {
  label: string;
  pct: number | null;
  right?: string;
}) {
  const has = pct != null;
  const clamped = has ? Math.min(100, Math.max(0, pct)) : 0;
  return (
    <div style={{ marginTop: 8 }}>
      <div
        className="flex-row"
        style={{ justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 3 }}
      >
        <span style={{ fontSize: 10, color: 'var(--vg-muted)', letterSpacing: 0.3, fontWeight: 700 }}>
          {label}
        </span>
        <span
          className="mono"
          style={{ fontSize: 11, fontWeight: 700, color: has ? 'var(--vg-ink)' : 'var(--vg-muted-2)' }}
        >
          {has ? `${clamped.toFixed(0)}% used · ${(100 - clamped).toFixed(0)}% left` : 'not sampled'}
          {right ? ` · ${right}` : ''}
        </span>
      </div>
      <div
        style={{
          height: 6,
          borderRadius: 3,
          background: 'var(--vg-hairline)',
          overflow: 'hidden',
        }}
      >
        {has && (
          <div style={{ width: `${clamped}%`, height: '100%', background: barColor(clamped) }} />
        )}
      </div>
    </div>
  );
}

export default function ResourceMeter({
  resources,
}: {
  resources?: AgentRow['resources'];
}) {
  if (resources == null) {
    return (
      <div style={{ fontSize: 10, color: 'var(--vg-muted-2)', marginTop: 8, lineHeight: 1.35 }}>
        No live worker: compute and memory are only reported by a heartbeating agent.
      </div>
    );
  }
  const { cpu_pct, memory_pct, memory_rss_bytes, memory_total_bytes } = resources;
  const memRight =
    memory_rss_bytes != null && memory_total_bytes != null
      ? `${formatBytes(memory_rss_bytes)} / ${formatBytes(memory_total_bytes)}`
      : undefined;
  return (
    <div>
      <Meter label="COMPUTE" pct={cpu_pct} />
      <Meter label="MEMORY" pct={memory_pct} right={memRight} />
    </div>
  );
}
