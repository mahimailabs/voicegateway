# acceptance-500: SYNTHETIC, an acceptance run that passes

**Nothing here was measured.** Every number is computed by `../make_synthetic.py`,
which is committed beside it so the arithmetic is reviewable. Do not read any
figure here as evidence about any deployment.

It exists because every REAL artifact in this repository is of a failed run, so
nothing had ever exercised the path where a run passes. A report generator that
has only ever been run against failures is a generator whose happy path is
unproven.

`capture-01` remains the authority on SHAPE. The header is copied from it byte
for byte, and the file is named the way the generator names it,
`gossipper_<pid>_stats.log`, with no `summary.json`, because that is what a real
run looks like.

## What it describes

One step holding 500 concurrent calls for six minutes. 15,000 attempted, 14,985
established: 99.9%, above the 99.5% floor and deliberately not 100%. A run with
no failures at all is rarer than a report that forgot to count them, and a
fixture that never exercises the failure columns is not testing them.

Paired with node samples inside the 70% CPU and 75% memory ceilings, this run's
verdict is PASS.

Six minutes, not two, and the length is load-bearing. A step shorter than
`capacity.MIN_STEADY_STATE_S` spends most of its wall time establishing calls
rather than holding them, so the CPU it records is a call-setup rate and the
capacity derivation excludes it. At two minutes this fixture's refusal below
would have been "the step was too short" rather than the reason it is written to
demonstrate.

## Why it cannot also produce a capacity table

`derive_calls_per_node` refuses a ramp that never saturated: the highest
concurrency observed is then a floor on the node's capacity and not a measure of
it, and sizing a fleet from it would under-provision. Measuring capacity means
exceeding the ceiling. Passing acceptance means staying under it. See
`../saturation-ramp/`, which is the other run.
