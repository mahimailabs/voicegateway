# Organisation minute allowances

Operator-issued entitlements carry an explicit inclusive start, exclusive end, minute limit,
active flag and monotonically increasing revision. Prices remain in the private control plane.
Only the instance token may write; organisation members may read their own status.

Enable with VOICEGATEWAY_CLOUD_QUOTAS=true (requires the instance token). Missing, inactive,
future or expired entitlements refuse uploads with 402; database failures return retryable 503.
Self-hosted defaults to no quota. Existing reads and customer calls remain unaffected.

Decision 0029 still governs measurement: closed observed calls use their end, inferred calls
use last activity, missing/negative durations contribute nothing. Sum seconds before rounding.
The durable per-call ledger survives telemetry deletion and updates idempotently with call writes.
Already accepted work and open calls may close after the cap: this is not an exact real-time cap
and never triggers automatic overage charges. Late corrections can change the measured period.

Warnings are 80%, 95% and 100%, visible in Organisation settings and ingestion response headers.
Custom uses an explicit operator-issued allowance, never an unlimited sentinel. Period changes
come from the verified subscription, not a calendar-month guess. Exact revision replays succeed;
stale revisions or changed bodies at the same revision return 409.

Acceptance: threshold and expiry tests; concurrent replays; retention, fractional duration and
period isolation tests in real Postgres; instance-only writes and organisation-only reads;
console warning and unknown-state tests. Subscription verification/renewal belongs to the private
Cloud integration and must pass before enabling enforcement in production.

At exhaustion, an active entitlement in its current period permits final telemetry for calls
already indexed in the authenticated project and overlapping that period. Every span, log record
and metric point must identify a known call; mixed known/new batches and unattributed records are
refused. Metrics use point identity, never the process resource. A finished indexed call remains
eligible for its late session report. Revocation and expiry still refuse all uploads. Identified calls are recorded durably before stream append, so a delayed worker does not lose
their admission. This is an admission receipt, not a duration reservation or an overage charge.
Unattributed uploads below the cap do not earn a call-specific continuation exception.
