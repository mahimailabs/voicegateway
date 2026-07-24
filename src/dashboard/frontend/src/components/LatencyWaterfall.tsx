// Horizontally-stacked latency waterfall for an agent's average first-byte
// latency. Only STT / LLM / TTS are measured (first-byte per modality); the
// network hops and turn-detection segments of a full colocation diagram are not
// metered, so the bar is honest about the three segments it can show.

type Stack = { stt: number | null; llm: number | null; tts: number | null };

const SEGMENTS = [
  { key: 'stt', label: 'STT', color: 'var(--vg-teal)' },
  { key: 'llm', label: 'LLM', color: 'var(--vg-green)' },
  { key: 'tts', label: 'TTS', color: 'var(--vg-red)' },
] as const;

export default function LatencyWaterfall({ latency }: { latency?: Stack | null }) {
  const parts = SEGMENTS.map((s) => ({ ...s, ms: latency?.[s.key] ?? 0 })).filter(
    (s) => s.ms > 0,
  );
  const total = parts.reduce((sum, s) => sum + s.ms, 0);

  if (total === 0) {
    return (
      <div style={{ fontSize: 11, color: 'var(--vg-muted-2)' }}>
        No latency samples yet
      </div>
    );
  }

  return (
    <div>
      <div
        className="flex-row"
        style={{ justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}
      >
        <span className="vg-card__label" style={{ fontSize: 11 }}>
          Avg first-byte latency
        </span>
        <span className="mono" style={{ fontSize: 12, fontWeight: 700, color: 'var(--vg-ink)' }}>
          {Math.round(total)}ms
        </span>
      </div>

      <div
        style={{
          display: 'flex',
          height: 22,
          borderRadius: 'var(--vg-radius-xs)',
          overflow: 'hidden',
          border: '1px solid var(--vg-hairline)',
        }}
      >
        {parts.map((s) => {
          const pct = (s.ms / total) * 100;
          return (
            <div
              key={s.key}
              title={`${s.label} ${Math.round(s.ms)}ms`}
              style={{
                width: `${pct}%`,
                background: s.color,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#fff',
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: 0.3,
                minWidth: 0,
              }}
            >
              {pct > 14 ? s.label : ''}
            </div>
          );
        })}
      </div>

      <div className="flex-row flex-wrap" style={{ gap: 10, marginTop: 6 }}>
        {parts.map((s) => (
          <span
            key={s.key}
            className="mono"
            style={{
              fontSize: 11,
              color: 'var(--vg-muted)',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
            }}
          >
            <span
              style={{ width: 8, height: 8, borderRadius: 2, background: s.color, display: 'inline-block' }}
            />
            {s.label} {Math.round(s.ms)}ms
          </span>
        ))}
      </div>
    </div>
  );
}
