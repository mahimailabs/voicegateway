import type { LogRecord } from '../lib/types';
import { latencyBadgeClass, statusBadgeClass, formatCost, formatMs } from '../lib/ui';

interface Props {
  logs: LogRecord[];
}

export default function LogTable({ logs }: Props) {
  if (!logs || logs.length === 0) {
    return (
      <div className="neo-card">
        <div className="empty-state">No requests logged yet</div>
      </div>
    );
  }
  return (
    <table className="neo-table neo-table--orange">
      <thead>
        <tr>
          <th>Time</th>
          <th>Model</th>
          <th>Type</th>
          <th>Cost</th>
          <th>Latency</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {logs.map((log, i) => (
          <tr key={log.id ?? i}>
            <td className="mono">{new Date(log.timestamp * 1000).toLocaleTimeString()}</td>
            <td className="mono">{log.model_id}</td>
            <td>
              <span className="neo-badge neo-badge--black">{(log.modality || '').toUpperCase()}</span>
            </td>
            <td className="mono">{formatCost(log.cost_usd, 6)}</td>
            <td>
              <span className={`neo-badge ${latencyBadgeClass(log.total_latency_ms)}`}>
                {formatMs(log.total_latency_ms)}
              </span>
            </td>
            <td>
              <span className={`neo-badge ${statusBadgeClass(log.status)}`}>{log.status}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
