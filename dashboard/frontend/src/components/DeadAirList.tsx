// DeadAirList: count of silences longer than threshold (REQ-VG-METRICS-004).
//
// v0.2.0 ships the aggregate count across the filter window. The
// per-session event list lives at /api/sessions/{id}/dead_air and is
// surfaced in the session drill-down view (still pending dashboard
// route work; the data endpoint is live as of T12). Showing the
// active threshold inline matches the Foundry's tooltip requirement
// and gives the developer the value to compare against.

interface Props {
  count: number;
  thresholdSeconds: number;
}

export default function DeadAirList({ count, thresholdSeconds }: Props) {
  return (
    <div className="neo-card neo-card--strip-red">
      <div className="label">Dead-air events</div>
      <div className="stat-value stat-value--xl mt-md">
        {count}
      </div>
      <div className="label mt-sm" style={{ opacity: 0.7 }}>
        Silences longer than {thresholdSeconds}s that the caller did not
        initiate. Drill into a session to see per-event timestamps.
      </div>
    </div>
  );
}
