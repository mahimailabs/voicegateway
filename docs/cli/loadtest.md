---
title: voicegw loadtest
description: Import an external SIP load generator's run artifacts and report on them, with provenance you cannot fake.
---

# voicegw loadtest

Import the artifacts an external SIP load generator wrote, correlate them with scraped node metrics, and emit a report.

VoiceGateway does not place calls and will not become a load generator. It is the evidence and reporting layer: something else drives the load, and this reads what it left behind.

## Synopsis

```bash
voicegw loadtest import ./run-artifacts          # one run, a row per test
voicegw loadtest import ./run-artifacts --captured   # declare the artifacts real
voicegw loadtest runs                            # what has been imported
voicegw loadtest report ramp-500                 # JSON + self-contained HTML
```

## Provenance under-claims by default

An import is recorded as **synthetic** unless you pass `--captured`.

The checksum of the artifacts is computed either way and written into the run's notes, so you can always tell which bytes a row came from. Only its promotion to `artifact_sha256` is gated, and that column is the single thing a report reads to decide whether it may call itself measured.

The default is deliberate. A forgotten flag produces an under-claim, never a report that presents fixture numbers as measurements. Any report built from a synthetic run carries `data_provenance: synthetic` and stamps `SYNTHETIC DATA: NOT A DELIVERABLE` as the first visible element of the HTML.

There is no flag that overrides this. A run that declares itself measured without a checksum is still synthetic, because nothing reads such a declaration.

## What gets imported

A directory holding artifacts is one test. A directory of subdirectories is one test each, sequenced by name so a ramp's steps stay in order. The run id defaults to the directory name, so re-importing the same artifacts updates the same run rather than creating a second one.

Two surfaces are read, and they are not interchangeable:

| Column | Source | Taking it from the wrong place |
|---|---|---|
| Peak concurrency | `max(active_calls)` across the per-interval CSV | the summary's `active_calls` is an end-of-run drain value and reads 0 |
| Establishment | the run-total counts | a per-row scan of `success_ratio` reads 0 from the first interval |
| Failures by cause | either surface, normalised | the summary nests them, the CSV keeps them flat |

Peak CPU and memory come from `node_samples` correlated by **time-window overlap**. They say what the fleet was doing while a test ran. They never say a node served a particular call, because nothing server-side can.

## Reproducing a run

Numbers are only evidence if somebody else can produce them again, so the report carries an appendix of commands and flag semantics, each requiring a citation. An uncited command is indistinguishable from one the report invented.

Supply it with `--appendix`, pointing at a JSON file you hold:

```json
{
  "commands": [
    {"label": "ramp step",
     "detail": "gossip sipp -l 200 -r 3.2258 -pause_ms 60000 -trace_stat",
     "citation": "runbook.md:75"}
  ],
  "flags": [
    {"label": "-l",
     "detail": "Max concurrent calls. Defaults to 1.",
     "citation": "runbook.md:53"}
  ],
  "toolchain": [
    {"label": "build",
     "detail": "Cross-compile for linux/amd64; does not build on macOS.",
     "citation": "observed on 0.1.61 and 0.1.62"}
  ]
}
```

The file lives with you rather than in this repository. Commands belong to a particular engagement, and the generator they drive is under a copyleft licence while this project is MIT, so its scenarios and configuration must not be copied here. Flag names and command lines are interface facts and travel fine.

Three sections are recognised: `commands`, `flags` and `toolchain`. A misspelled one is refused rather than ignored, because silently dropping a section would hand over a report missing the commands that produced it. An entry with no citation is refused by position so you can find the line.

Run `voicegw loadtest report` without `--appendix` and it says so rather than omitting the section quietly.

Scenario files and generator configuration are referenced by name, never copied. They are the work of whoever authored them, and a generator under a copyleft licence has no business having its source pasted into an MIT repository. Run it as a separate binary, ingest its output files.

Absolute URLs in the appendix are reduced to a bare host label before rendering, both because an absolute URL breaks the self-containment guarantee and because a report handed to somebody outside the deployment should not carry its endpoints.

## Flag semantics are worth verifying

Load generators in this space have flag defaults that surprise people, and a generator's own documentation can disagree with its behaviour. Two failures worth knowing about, because both produce a run that completes and measures the wrong thing:

- **A concurrency flag that defaults to 1.** Omit it and the entire run is capped at one concurrent call while every other number looks reasonable.
- **A fixed arrival rate across a rising ramp.** By Little's law a generator sustains `rate x duration` concurrent calls, so a plan that raises the target without raising the rate plateaus, and that plateau is the generator's ceiling rather than the node's.

The second is caught for you: `voicegw` refuses to derive a calls-per-node figure from a plateaued ramp, and reports the rate each unreachable step would have needed. Sizing a fleet from a false ceiling buys the wrong number of machines, and no later check can catch it because every measurement in such a run is internally consistent.

Verify flags against `-h` on the binary you are actually holding, not against its README.

## A ramp step must be long enough to measure concurrency

A step shorter than five minutes does not contribute to the calls-per-node figure, and a run whose every step is that short gets no capacity table at all.

Short steps spend most of their wall time establishing calls rather than holding them. Signalling costs far more CPU per call than carrying one does, so such a step reports a call **setup rate** wearing a concurrency limit's label. One observed run recorded 25 concurrent at 83.8% CPU over a 110-second step, and 100 concurrent at 66.5% on the same single node over a soak. Sized from the ramp, 100 calls needs nine nodes; the soak shows one node doing it.

The error runs one way and nobody gets paged for it: setup-dominated CPU crosses the ceiling at a lower concurrency, so the figure comes out small and the fleet is sized too large.

Hold each step at its target concurrency long enough that arrivals and departures balance. A step whose duration was not recorded is kept rather than excluded, and the report says so: not knowing how long something ran is not evidence that it was short.

## What the report contains

The default view profiles. It opens with an **At a glance** table, one line per thing an operator decides on, and every line in it carries a measured number: anything the run did not measure is absent from that table rather than shown blank. `--acceptance` adds the verdict, the per-gate table and a non-zero exit code, for the case where somebody contracted thresholds.

Measurements the run did not collect are counted in the HTML and listed individually in the JSON under `not_collected`, each with its cause and what to change. They are not tabled in the document: every one of them describes how the run was configured rather than what the fleet did, and a thirteen-target fleet produces over a hundred.

What no run can ever collect is a separate claim, stated as prose under "What this report structurally does not measure" and carried in `scope_exclusions`. The two must not be confused: one is work somebody can do, the other is a fact about the world.

## Building the generator

If the generator does not compile on your machine, check whether the failure is confined to a non-Linux fallback path before spending time on it. A build error in a file carrying a `//go:build !linux` constraint, where a working `_linux.go` sibling exists, means the tool is fine on the rig you will actually run it on. Cross-compile for the load generator's platform and install it there.

Never run a load generator from a laptop. It is the thing under measurement as much as the deployment is.

## Where reports are written

`voicegw loadtest report` writes into `.artifacts/capacity-evidence/` by default, which is gitignored. A report committed to a repository is one somebody will later open and mistake for a measured run, long after the fixtures behind it are forgotten.

## See also

- [`voicegw livekit`](/cli/livekit) for probing a deployment directly
- [Storage](/architecture/storage) for where runs and node samples are kept
