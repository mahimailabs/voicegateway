---
title: voicegw loadtest
description: Import an external SIP load generator's run artifacts, correlate them with scraped node metrics, and report on them with provenance that defaults to synthetic unless the operator declares the run captured.
---
VoiceGateway does not place calls, and `loadtest` does not either. An external SIP load generator drives the load: this reads what it left on disk, correlates it against scraped fleet metrics, and writes a report. VoiceGateway vendors no such generator. See [What you can profile](/guide/what-you-can-profile) for how this fits the rest of the stack.

## Synopsis

```bash
voicegw loadtest import ./run-artifacts --captured
voicegw loadtest runs
voicegw loadtest report ramp-500 --acceptance
```

## import

`voicegw loadtest import DIRECTORY` reads one test's artifacts. A directory of subdirectories imports one test per subdirectory, sorted lexicographically by name, not numerically. Pad step numbers to equal width (`ramp-0250`, not `ramp-250`): unpadded, `ramp-1000` sorts before `ramp-250`. Re-importing the same directory updates that run (id defaults to the directory name) rather than creating a second one.

**What the directory must hold.** Either surface below is enough alone; both absent, nothing imports:

- `summary.json`, schema `gossipper_summary_v1`: run totals.
- A stat CSV, found by header (not name or extension), carrying `elapsed_ms`, `total_calls`, `success_calls`, `failed_calls`, `active_calls`.

`calls.jsonl` (schema `gossipper_call_record_v1`) is optional: per-call media, read only to count calls that answered, sent RTP, and got none back.

**Flags**

| Flag | Does |
|---|---|
| `--config, -c` | Path to `voicegw.yaml`. |
| `--run-id` | Run id. Defaults to the directory name. |
| `--project, -p` | Project id. Default `default`. |
| `--label, -l` | Human label for the run. |
| `--captured` | Declare the artifacts came from a real run. See Provenance below. |
| `--node` | Correlate against one node only. Default: all. |
| `--plan` | JSON file declaring each step's target concurrency. See below. |
| `--network-baseline` | JSON file declaring each node's published bandwidth. See below. |
| `--dry-run` | Print what would be written; write nothing. |

### Provenance defaults to synthetic

An import is recorded **synthetic** unless `--captured` is passed. The checksum is computed either way, but only `--captured` promotes it to `artifact_sha256`, the column a report reads to decide whether it may call itself measured. Without it, every report built from the run stamps `data_provenance: synthetic`, with `SYNTHETIC DATA: NOT A DELIVERABLE` as the first visible element of the HTML. No flag overrides this: a forgotten `--captured` under-claims; it never lets a report pass fixture numbers off as measured.

### `--plan` and `--network-baseline` are declarations, not measurements

Neither is recorded by any artifact. Both are the operator writing down what nothing on the wire can measure.

**`--plan`**: test name to declared target concurrency, powering the capacity table across a multi-step ramp.

```json
{"ramp-250": 250, "ramp-500": {"target_concurrency": 500, "rate_per_second": 8.3, "hold_seconds": 300}}
```

The peak a step actually reached still comes from the artifacts, never overwritten by the plan; a declared target the run did not reach produces a plateau finding, not a capacity figure. A step under five minutes measures call-setup CPU, not held concurrency, and does not count toward the figure. An unmatched test name is refused.

**`--network-baseline`**: node name to its published bandwidth floor, both directions required, because the AWS ENA driver publishes no link speed.

```json
{"sip-1": {"in_bps": 3125000000, "out_bps": 3125000000}}
```

It is the only source for the bandwidth-headroom gate's denominator. A node with no entry reports UNKNOWN, never a percentage of a guess. It is a floor, not a ceiling: an instance bursts above it, so utilisation computed against it reads high, the safe direction for a headroom check.

## runs

`voicegw loadtest runs` lists imported runs, newest first, with each row's provenance read off whether `artifact_sha256` is set. Flags: `--config, -c`; `--project, -p` to filter; `--limit, -n` for how many (default 20).

## report

`voicegw loadtest report RUN_ID` writes one run's profile as JSON plus a self-contained HTML file. `RUN_ID` is a required positional argument.

| Flag | Does |
|---|---|
| `--config, -c` | Path to `voicegw.yaml`. |
| `--out, -o` | Output directory. Default `.artifacts/capacity-evidence`, gitignored. |
| `--appendix` | JSON file of reproducible-test assets (commands, flags, toolchain), each entry citation-required. Omitted, the report says so rather than silently dropping the section. |
| `--waive` | JSON file mapping a gate (or `gate/subject`) to a written reason it was waived. Only meaningful with `--acceptance`; never turns a gate into a pass. An unmatched key is refused. |
| `--acceptance` | Judge the run against contracted thresholds: adds the verdict, the per-gate table, and the exit code below. Without it the report only profiles what was measured, with no verdict. |

### Exit code

`--acceptance` exits `0` only on a clean **PASS**. **WAIVED**, **WARN**, **UNKNOWN**, and **FAIL** all exit `1`. WAIVED is not clean: a gate nobody held the run to should not turn a pipeline green. UNKNOWN is not clean either: the run failed to *measure* something, not the same as demonstrating it passed. Without `--acceptance`, `report` never exits non-zero; a profile makes no claim to judge.

## Node correlation

Peak CPU and memory come from `node_samples`, correlated to a test's window by time overlap, never call attribution. That table is filled by a background scrape that runs only inside a long-lived [`voicegw serve`](/cli/serve) process with `VOICEGW_NODE_SCRAPE_TARGETS` (or `_FILE`) configured. `loadtest` never scrapes on its own; it reads only what a running `serve` already collected. See [Node metrics](/guide/node-metrics) for configuring the scrape.
