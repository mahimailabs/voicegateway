// Diagnostics > SFU: the idle baseline, two synthetic clients through the SFU.
//
// One measured number (a data-channel round trip, which needs no clock sync
// between the two clients) and one coarse SDK signal (quality). Packet loss is
// deliberately absent: `sfu.py` reports a hardcoded 0.0 because the SDK does not
// expose per-connection loss, and drawing that as a flat 0% line would be a
// fabricated clean bill of health. It is labelled "not measured" instead.

import type { DiagSfuResult, DiagnosticCheckResult } from '../../lib/types';
import { Card, CheckGate, Note, NOT_MEASURED, qualityBadgeClass, Row, Stat } from './shared';

export default function SfuTab({
  check,
}: {
  check: DiagnosticCheckResult<DiagSfuResult> | undefined;
}) {
  return (
    <CheckGate
      check={check}
      label="SFU baseline"
      missingHint="Tick “SFU baseline” above and run the checks again."
    >
      {(result) => <BaselineCard result={result} />}
    </CheckGate>
  );
}

function BaselineCard({ result }: { result: DiagSfuResult }) {
  const { rtt_ms, quality } = result.baseline;
  const degraded = quality === 'Poor' || quality === 'Lost';
  return (
    <Card
      label="SFU baseline (2 clients, idle)"
      right={<span className={`neo-badge ${qualityBadgeClass(quality)}`}>{quality}</span>}
    >
      <Stat
        value={rtt_ms.toFixed(1)}
        unit="ms"
        caption="Round trip through the SFU, measured on the data channel between two synthetic clients"
      />

      <div style={{ marginTop: 14 }}>
        <Row label="Connection quality (SDK)" value={quality} />
        <Row label="Packet loss" value={NOT_MEASURED} muted />
        <Row
          label="Budget used for the ramp knee"
          value={`${result.target_rtt_ms.toFixed(0)} ms`}
        />
      </div>

      <div style={{ marginTop: 12 }}>
        <Note>
          Loss is not measured and no loss series is drawn: the client SDK does not expose
          per-connection loss here, so the probe reports a placeholder rather than a number.
          Quality is the coarse signal that is real.
        </Note>
      </div>

      {degraded && (
        <div style={{ marginTop: 12 }}>
          <Note tone="warn">
            A baseline quality of {quality} with no load on the SFU is what turns this run’s
            verdict to WARN. Fix the idle path before reading anything into the ramp.
          </Note>
        </div>
      )}
    </Card>
  );
}
