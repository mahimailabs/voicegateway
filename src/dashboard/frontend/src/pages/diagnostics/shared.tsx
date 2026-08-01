// Shared primitives for the Diagnostics result tabs.
//
// Each tab renders the payload of ONE check from one run, and each has to answer
// three states honestly: the check was never part of the run, the check ran and
// failed, or the check produced data. `CheckGate` is that fork in one place, so
// no tab can quietly render an empty card for a check nobody ran (the failure
// mode a compile-only frontend gate cannot see).

import type { ReactNode } from 'react';
import type { DiagnosticCheckResult } from '../../lib/types';

/** What a value says when it was not measured. Never render 0 in its place. */
export const NOT_MEASURED = 'not measured';

export type Tone = 'muted' | 'good' | 'warn' | 'bad';

const TONE_COLOR: Record<Tone, string> = {
  muted: 'var(--vg-muted)',
  good: 'var(--vg-green)',
  warn: 'var(--vg-amber)',
  bad: 'var(--vg-red)',
};

const TONE_TINT: Record<Tone, string> = {
  muted: 'transparent',
  good: 'var(--vg-green-tint)',
  warn: 'var(--vg-amber-tint)',
  bad: 'var(--vg-red-tint)',
};

export function Card({
  label,
  right,
  children,
}: {
  label: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="vg-card" style={{ marginBottom: 16 }}>
      <div
        className="flex-row"
        style={{ justifyContent: 'space-between', alignItems: 'center', gap: 10, marginBottom: 10 }}
      >
        <div className="vg-card__label">{label}</div>
        {right}
      </div>
      {children}
    </div>
  );
}

/** A paragraph of explanation. `muted` is plain text; the rest are banners. */
export function Note({ tone = 'muted', children }: { tone?: Tone; children: ReactNode }) {
  const banner = tone !== 'muted';
  return (
    <div
      style={{
        fontSize: 13,
        lineHeight: 1.55,
        color: TONE_COLOR[tone],
        background: TONE_TINT[tone],
        borderLeft: banner ? `3px solid ${TONE_COLOR[tone]}` : undefined,
        padding: banner ? '9px 12px' : 0,
        borderRadius: banner ? 'var(--vg-radius-xs)' : undefined,
      }}
    >
      {children}
    </div>
  );
}

/** A label/value pair. `muted` marks a value that could not be measured. */
export function Row({
  label,
  value,
  muted,
}: {
  label: string;
  value: ReactNode;
  muted?: boolean;
}) {
  return (
    <div
      className="flex-row"
      style={{ justifyContent: 'space-between', alignItems: 'baseline', gap: 12, padding: '3px 0' }}
    >
      <span style={{ fontSize: 12, color: 'var(--vg-muted)' }}>{label}</span>
      <span
        className="mono"
        style={{
          fontSize: 13,
          fontWeight: 700,
          color: muted ? 'var(--vg-muted-2)' : 'var(--vg-ink)',
        }}
      >
        {value}
      </span>
    </div>
  );
}

/** Big single number with its unit and a caption underneath. */
export function Stat({
  value,
  unit,
  caption,
}: {
  value: string;
  unit?: string;
  caption: string;
}) {
  return (
    <div>
      <div className="mono" style={{ fontSize: 30, fontWeight: 800, lineHeight: 1.1 }}>
        {value}
        {unit && (
          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--vg-muted)' }}> {unit}</span>
        )}
      </div>
      <div style={{ fontSize: 12, color: 'var(--vg-muted)', marginTop: 4 }}>{caption}</div>
    </div>
  );
}

/** Badge class for the SDK's coarse connection quality. */
export function qualityBadgeClass(quality: string): string {
  if (quality === 'Excellent' || quality === 'Good') return 'neo-badge--green';
  if (quality === 'Poor' || quality === 'Lost') return 'neo-badge--red';
  return 'neo-badge--black'; // Unknown: the SDK reported nothing usable
}

/**
 * Badge class for one stored verdict. Never re-judges: the verdict is whatever
 * `livekit_diag.gates` recorded when the run executed.
 *
 * It lives here, beside `qualityBadgeClass`, because two surfaces render the
 * same run's verdict: the run-history table and the summary pill on
 * `Diagnostics`, and the header on `ReportTab`. Those were separate copies, and
 * they disagreed the moment one of them learned about UNKNOWN.
 *
 * UNKNOWN has its own case because it is a real, reachable verdict and not a
 * missing one: `gates.verdict` returns it when nothing could be evaluated, and
 * `gates.exit_code` still exits non-zero for it. Every other treatment in this
 * kit would misreport it. Green and teal read as healthy, and a run that
 * measured nothing has not passed. Amber is WARN's, a different and strictly
 * lesser severity. Ink is what this very badge already shows for a run with NO
 * verdict at all: `RunSummary` renders `verdict ?? status`, so a run that never
 * produced one puts "failed" / "running" in the same pill, and UNKNOWN sharing
 * it would collapse "could not tell" into "never got that far". That leaves the
 * kit's colourless badge, which is the point: it makes no claim, and no claim is
 * all an unevaluated run has earned.
 */
export function verdictBadgeClass(v: string | null): string {
  if (v === 'PASS') return 'neo-badge--green';
  if (v === 'WARN') return 'neo-badge--warning';
  if (v === 'FAIL') return 'neo-badge--red';
  if (v === 'UNKNOWN') return 'neo-badge--white';
  // No verdict, or one this build does not recognise. Not a health claim either.
  return 'neo-badge--black';
}

export function CheckGate<R>({
  check,
  label,
  missingHint,
  children,
}: {
  check: DiagnosticCheckResult<R> | undefined;
  label: string;
  /** What the operator should do to get this surface populated. */
  missingHint: string;
  children: (result: R) => ReactNode;
}) {
  if (check === undefined) {
    return (
      <Card label={label}>
        <Note>This run did not include the check, so there is nothing measured to show. {missingHint}</Note>
      </Card>
    );
  }
  if (!check.ok || check.result === undefined) {
    return (
      <Card label={label} right={<span className="neo-badge neo-badge--red">failed</span>}>
        <Note tone="bad">
          The check did not complete: {check.error ?? 'no result was recorded for it'}
        </Note>
      </Card>
    );
  }
  return <>{children(check.result)}</>;
}
