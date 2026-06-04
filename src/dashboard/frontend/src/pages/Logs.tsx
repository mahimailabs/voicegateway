import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import LogTable from '../components/LogTable';
import FilterBar, { useAgentFilter } from '../components/FilterBar';
import { fetchJson } from '../lib/api';
import type { LogRecord } from '../lib/types';

export default function Logs() {
  const [logs, setLogs] = useState<LogRecord[]>([]);
  const [filter, setFilter] = useState<string>('');
  const agent = useAgentFilter();

  useEffect(() => {
    const params = new URLSearchParams({ limit: '50' });
    if (filter) params.set('modality', filter);
    if (agent !== null) params.set('agent', agent);
    fetchJson<LogRecord[]>(`/api/logs?${params.toString()}`)
      .then(setLogs)
      .catch(() => setLogs([]));
  }, [filter, agent]);

  return (
    <div>
      <PageHeader title="Logs" subtitle="Recent inference requests" accent="orange" />

      <FilterBar showTenant={false} />

      <div className="filter-bar">
        <span className="label">Modality</span>
        <select className="neo-select" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All</option>
          <option value="stt">STT</option>
          <option value="llm">LLM</option>
          <option value="tts">TTS</option>
        </select>
        <button className="neo-btn neo-btn--orange">Apply</button>
        <button className="neo-btn" onClick={() => setFilter('')}>
          Reset
        </button>
      </div>

      <LogTable logs={logs} />

      <div className="flex-row mt-lg">
        <button className="neo-btn">← Previous</button>
        <button className="neo-btn neo-btn--orange">Next →</button>
      </div>
    </div>
  );
}
