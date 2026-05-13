import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import LogTable from '../components/LogTable';
import { fetchJson } from '../lib/api';
import type { LogRecord } from '../lib/types';

export default function Logs() {
  const [logs, setLogs] = useState<LogRecord[]>([]);
  const [filter, setFilter] = useState<string>('');

  useEffect(() => {
    const url = filter ? `/api/logs?limit=50&modality=${filter}` : '/api/logs?limit=50';
    fetchJson<LogRecord[]>(url).then(setLogs).catch(() => setLogs([]));
  }, [filter]);

  return (
    <div>
      <PageHeader title="Logs" subtitle="Recent inference requests" accent="orange" />

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
