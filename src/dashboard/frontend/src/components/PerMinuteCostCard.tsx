// PerMinuteCostCard: single-number cost-per-minute display (REQ-VG-METRICS-001).
//
// v0.2.0 ships the single average across the filter window. The Foundry's
// "provider-modality breakdown" and "trend vs prior period" sub-features
// are deferred: the breakdown needs a /api/metrics-by-modality endpoint
// that does not exist yet, and the trend needs a comparison query across
// two windows. Both can land as follow-ups without disturbing this card's
// shape — the existing `value` prop subsumes the trend headline number.

interface Props {
  value: number | null;
  measuredSessionCount: number;
}

export default function PerMinuteCostCard({ value, measuredSessionCount }: Props) {
  return (
    <div className="neo-card neo-card--strip-green">
      <div className="label">Per-minute cost</div>
      <div className="stat-value stat-value--xl mt-md">
        {value !== null ? (
          `$${value.toFixed(4)}`
        ) : (
          <span className="empty-state-inline">not measured</span>
        )}
      </div>
      <div className="label mt-sm" style={{ opacity: 0.7 }}>
        Avg across {measuredSessionCount} session
        {measuredSessionCount === 1 ? '' : 's'}
      </div>
    </div>
  );
}
