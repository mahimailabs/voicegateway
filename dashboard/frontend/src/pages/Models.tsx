import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import { fetchJson } from '../lib/api';
import type { StatusResponse } from '../lib/types';

export default function Models() {
  const [data, setData] = useState<StatusResponse | null>(null);

  useEffect(() => {
    fetchJson<StatusResponse>('/api/status').then(setData).catch(() => setData(null));
  }, []);

  if (!data) return <div className="empty-state">Loading models...</div>;

  const entries = Object.entries(data.models);

  return (
    <div>
      <PageHeader title="Models" subtitle={`${entries.length} configured`} accent="blue" />
      <table className="neo-table neo-table--blue">
        <thead>
          <tr>
            <th>Model</th>
            <th>Modality</th>
            <th>Provider</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([id, cfg]) => {
            const configured = data.providers[cfg.provider]?.configured;
            return (
              <tr key={id}>
                <td className="mono">{id}</td>
                <td>
                  <span className="neo-badge neo-badge--black">
                    {(cfg.modality || '').toUpperCase()}
                  </span>
                </td>
                <td>
                  <span className="neo-badge neo-badge--blue">{cfg.provider}</span>
                </td>
                <td>
                  <span className={`neo-badge ${configured ? 'neo-badge--online' : 'neo-badge--offline'}`}>
                    {configured ? 'Ready' : 'No API Key'}
                  </span>
                </td>
              </tr>
            );
          })}
          {entries.length === 0 && (
            <tr>
              <td colSpan={4} className="empty-state">
                No models configured.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
