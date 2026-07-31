// Horizontal bars of failures grouped by class, with the first couple of real
// messages under each bar.
//
// The bars are proportional to the LARGEST class in the set, not to the number of
// runs: this answers "which failure dominates", and the count next to each label
// is the absolute number so the relative bar cannot be misread as a rate.
// Nothing is drawn for a class with zero failures - an empty bar would suggest
// something was checked and found clean, which is a different claim.

import {
  ERROR_CLASS_COLOR,
  ERROR_CLASS_LABEL,
  ERROR_CLASS_ORDER,
  type ClassifiedError,
  type ErrorClass,
} from '../lib/errorClass';

interface ClassBar {
  klass: ErrorClass;
  count: number;
  examples: string[];
}

/** Group in display order, dropping the classes nothing landed in. */
function toBars(errors: ClassifiedError[]): ClassBar[] {
  return ERROR_CLASS_ORDER.map((klass) => {
    const hits = errors.filter((e) => e.klass === klass);
    return {
      klass,
      count: hits.length,
      examples: hits.map((e) => `${e.where}: ${e.message}`),
    };
  }).filter((bar) => bar.count > 0);
}

function truncate(text: string, max = 130): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

export default function ErrorClassChart({ errors }: { errors: ClassifiedError[] }) {
  const bars = toBars(errors);
  const max = bars.reduce((m, b) => Math.max(m, b.count), 0);

  if (bars.length === 0) return null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {bars.map((bar) => (
        <div key={bar.klass}>
          <div
            className="flex-row"
            style={{ justifyContent: 'space-between', alignItems: 'baseline', gap: 10 }}
          >
            <span style={{ fontSize: 13, fontWeight: 600 }}>{ERROR_CLASS_LABEL[bar.klass]}</span>
            <span className="mono" style={{ fontSize: 13, fontWeight: 700 }}>
              {bar.count}
            </span>
          </div>
          <div
            style={{
              height: 10,
              marginTop: 5,
              borderRadius: 'var(--vg-radius-xs)',
              background: 'var(--vg-hairline)',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                width: `${max === 0 ? 0 : (bar.count / max) * 100}%`,
                height: '100%',
                background: ERROR_CLASS_COLOR[bar.klass],
              }}
            />
          </div>
          <div style={{ marginTop: 5 }}>
            {bar.examples.slice(0, 2).map((example, i) => (
              <div
                key={i}
                className="mono"
                style={{ fontSize: 11, color: 'var(--vg-muted)', lineHeight: 1.5 }}
              >
                {truncate(example)}
              </div>
            ))}
            {bar.examples.length > 2 && (
              <div style={{ fontSize: 11, color: 'var(--vg-muted-2)' }}>
                +{bar.examples.length - 2} more like this
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
