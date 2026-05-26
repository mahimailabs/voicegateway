import styles from './CostRow.module.css';

export type CostRowItem = {
  modality: 'stt' | 'llm' | 'tts' | string;
  provider: string;
  model: string;
  units?: number;
  unitLabel?: string;
  inputUnits?: number;
  outputUnits?: number;
  subtotalUsd?: number;
  costUsd?: number;
};

export type CostRowResult = {
  callId: string;
  sessionId: string;
  rows: CostRowItem[];
  totalUsd: number;
  currency: 'USD' | string;
};

function formatUsd(value: number, currency: string) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatUnits(row: CostRowItem) {
  if (typeof row.units === 'number') {
    return `${row.units.toLocaleString('en-US')} ${row.unitLabel ?? 'units'}`;
  }

  const parts = [];
  if (typeof row.inputUnits === 'number') {
    parts.push(`${row.inputUnits.toLocaleString('en-US')} input`);
  }
  if (typeof row.outputUnits === 'number') {
    parts.push(`${row.outputUnits.toLocaleString('en-US')} output`);
  }

  return parts.length > 0 ? parts.join(' / ') : 'units pending';
}

function labelFor(modality: string) {
  const labels: Record<string, string> = {
    stt: 'Speech to text',
    llm: 'LLM',
    tts: 'Text to speech',
  };

  return labels[modality] ?? modality;
}

export default function CostRow({ result }: { result: CostRowResult }) {
  return (
    <div className={styles.vgCostRow}>
      <div className={styles.vgCostHead}>
        <p className={styles.vgCostTitle}>Cost row</p>
        <span className={styles.vgCostMeta}>session {result.sessionId}</span>
        <span className={styles.vgCostMeta}>call {result.callId}</span>
      </div>

      <div className={styles.vgCostList}>
        {result.rows.map((row) => (
          <div className={styles.vgCostItem} key={`${row.modality}-${row.provider}-${row.model}`}>
            <div>
              <p className={styles.vgCostModality}>{labelFor(row.modality)}</p>
              <p className={styles.vgCostProvider}>
                {row.provider} / {row.model}
              </p>
            </div>
            <p className={styles.vgCostUnits}>{formatUnits(row)}</p>
            <span className={styles.vgCostAmount}>
              {formatUsd(row.subtotalUsd ?? row.costUsd ?? 0, result.currency)}
            </span>
          </div>
        ))}
      </div>

      <div className={styles.vgCostTotal}>
        <span className={styles.vgCostTotalLabel}>Total demo call cost</span>
        <span className={styles.vgCostTotalAmount}>
          {formatUsd(result.totalUsd, result.currency)}
        </span>
      </div>
    </div>
  );
}
