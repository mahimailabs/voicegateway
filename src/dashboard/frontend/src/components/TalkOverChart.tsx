// TalkOverChart: percentage of agent speech overlapping caller speech (REQ-VG-METRICS-003).
//
// v0.2.0 ships the average rate as a single number with an explanatory
// tooltip about the threshold. The Foundry's sparkline-trend
// sub-feature is deferred: a sparkline needs per-day buckets across the
// window which the /api/metrics endpoint does not currently produce.
// Adding a `daily_buckets` array to the endpoint's response shape is a
// follow-up that lands without breaking this component's prop contract.

interface Props {
  rate: number | null;
  thresholdMs: number;
}

export default function TalkOverChart({ rate, thresholdMs }: Props) {
  return (
    <div className="vg-card">
      <div className="vg-card__label">Talk-over rate</div>
      <div className="vg-stat mt-md">
        {rate !== null ? (
          `${(rate * 100).toFixed(1)}%`
        ) : (
          <span style={{ fontSize: 14, color: 'var(--vg-muted)' }}>not measured</span>
        )}
      </div>
      <div className="vg-card__label mt-sm" style={{ opacity: 0.7 }}>
        Counts a turn as overlap when caller speech starts within
        {' '}{thresholdMs}ms of the prior agent speech finishing.
        Lower is better.
      </div>
    </div>
  );
}
