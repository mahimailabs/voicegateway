---
title: "baseline"
description: "Pin a known-good window of figures, then fail CI when a later one drifts."
---

Latency and cost regressions are the ones that ship, because nothing fails when they do. A test suite catches an agent that is broken. Nothing catches an agent that got 300ms slower or 40% more expensive per call, and the caller feels the first while the bill shows the second.

## pin

`voicegw baseline pin` writes the current window's figures to a file.

| Flag | Does |
|---|---|
| `--config, -c` | Path to `voicegw.yaml`. |
| `--period` | `today`, `week` (default), `month`, `all`. |
| `--project, -p` | Filter by project. |
| `--out, -o` | Output file. Default `voicegw-baseline.json`. |

The file records **what identifies the window**, not just the numbers, so a baseline is reproducible rather than a snapshot of an unnamed moment. Each metric carries the sample count it was taken over, because a percentile over three calls and one over two hundred are different claims and a file holding only the value cannot tell them apart afterwards.

## check

`voicegw baseline check` recomputes over a later window and exits non-zero on drift. It defaults to the pinned window, so a check without `--period` compares like with like.

<Note>
**Exit codes are stable.** CI depends on them, so they are API rather than an implementation detail:

- **0** every compared metric is within tolerance
- **1** at least one metric drifted, named with its delta
- **2** nothing drifted, but at least one metric could not be compared

Anything added later takes a new number rather than renumbering these.
</Note>

## What it refuses to call a pass

A comparison that cannot be made must not read as a pass, because a green exit is indistinguishable from a real one to the job reading it.

- **Too few samples** reports insufficient data rather than passing. Three calls against a baseline of two hundred is not a result.
- **A metric missing from the new window** is unmeasured and named. This is the case where something quietly stopped being collected.
- **A pinned zero** is reported rather than divided by: everything is an infinite increase from zero.
- **An improvement beyond tolerance** is reported too. A 50% drop is either very good news or a measurement that broke, and both deserve a human look.
- **Drift outranks insufficiency.** If one metric regressed and another had too few samples, the exit code is 1, so the real finding is not buried behind a warning.

A metric the new window *grew* is not a failure. Only metrics present in the pinned file are compared, otherwise every measurement added to VoiceGateway would break every existing baseline on upgrade, which trains people to delete the gate.

## Tolerances

Per metric, not one global number: cost and p95 do not move for the same reasons and do not deserve the same slack.

| Metric | Default |
|---|---|
| `response_speed_p50_ms` | 15% |
| `response_speed_p95_ms` | 20% |
| `llm_ttft_p95_ms` | 20% |
| `tts_ttfb_p95_ms` | 20% |
| `cost_per_call_usd` | 10% |

These are ours, not contracted. They are a starting point that fails loudly, not a threshold anybody measured.
