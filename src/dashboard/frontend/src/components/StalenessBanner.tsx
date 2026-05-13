// Q7 visual: warns when STT or TTS catalog dates have crossed a
// 30-day staleness threshold. Surfaces on Costs, Overview, Sessions,
// and (v0.2.0) Metrics pages — every view where the user reads
// dollars or cost-derived numbers. Hidden elsewhere to keep ambient
// noise low.
//
// The 60-day fail line is enforced by tests/pricing/test_staleness.py
// in CI; this banner gives operators a 30-day heads-up before that
// gate fires.
//
// Placement is per-page: each page imports and renders the banner
// near the top of its layout. The banner itself returns null when no
// catalog is stale, so adding a new page is just an import + render.

import { useEffect, useState } from 'react';
import { fetchJson } from '../lib/api';
import type { StatusResponse } from '../lib/types';

const STALENESS_DAYS = 30;

export default function StalenessBanner() {
  const [status, setStatus] = useState<StatusResponse | null>(null);

  useEffect(() => {
    fetchJson<StatusResponse>('/api/status')
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  const stale = pickStaleEntry(status);
  if (!stale) return null;

  return (
    <div
      role="status"
      className="staleness-banner"
      style={{
        background: '#FFF4D1',
        border: '2px solid var(--ink, #000)',
        boxShadow: '4px 4px 0 var(--ink, #000)',
        padding: '12px 16px',
        marginBottom: 16,
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        fontSize: 13,
      }}
    >
      <span
        aria-hidden="true"
        style={{
          display: 'inline-flex',
          width: 22,
          height: 22,
          borderRadius: '50%',
          background: '#FFD166',
          border: '1px solid var(--ink, #000)',
          alignItems: 'center',
          justifyContent: 'center',
          fontWeight: 700,
        }}
      >
        !
      </span>
      <span>
        {stale.label} estimates last verified <strong>{stale.date}</strong>
        {' '}({stale.daysOld} days old). Refresh per{' '}
        <a
          href="https://github.com/mahimailabs/voicegateway/blob/main/docs/contributing/refreshing-pricing.md"
          target="_blank"
          rel="noopener noreferrer"
          className="mono"
        >
          docs/contributing/refreshing-pricing.md
        </a>{' '}
        to keep the costs view honest; CI fails when an entry crosses 60
        days.
      </span>
    </div>
  );
}

function pickStaleEntry(status: StatusResponse | null): null | {
  label: string;
  date: string;
  daysOld: number;
} {
  if (!status?.pricing) return null;
  const candidates: { label: string; date?: string }[] = [
    { label: 'STT', date: status.pricing.stt?.oldest_entry_date },
    { label: 'TTS', date: status.pricing.tts?.oldest_entry_date },
  ];
  let worst: { label: string; date: string; daysOld: number } | null = null;
  const today = Date.now();
  for (const c of candidates) {
    if (!c.date) continue;
    const ts = Date.parse(c.date);
    if (Number.isNaN(ts)) continue;
    const daysOld = Math.floor((today - ts) / (1000 * 60 * 60 * 24));
    if (daysOld < STALENESS_DAYS) continue;
    if (!worst || daysOld > worst.daysOld) {
      worst = { label: c.label, date: c.date, daysOld };
    }
  }
  return worst;
}
