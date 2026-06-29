import { useSearchParams } from 'react-router-dom';

const OPTIONS = [
  { value: 'today', label: 'Today' },
  { value: 'week', label: '7 days' },
  { value: 'month', label: '30 days' },
  { value: 'all', label: 'All' },
] as const;

const VALID = new Set(OPTIONS.map((o) => o.value));

/** Read the URL-synced period (defaults to 'today'). Shareable via ?period=. */
export function usePeriod(): string {
  const [params] = useSearchParams();
  const p = params.get('period');
  return p && VALID.has(p as (typeof OPTIONS)[number]['value']) ? p : 'today';
}

/** Segmented time-range control, synced to ?period= in the URL. */
export default function TimeRange() {
  const [params, setParams] = useSearchParams();
  const current = usePeriod();
  return (
    <div className="seg" role="tablist" aria-label="Time range">
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={current === o.value}
          className={`seg__btn${current === o.value ? ' seg__btn--on' : ''}`}
          onClick={() => {
            const next = new URLSearchParams(params);
            next.set('period', o.value);
            setParams(next, { replace: true });
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
