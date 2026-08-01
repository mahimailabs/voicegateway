# capture-01: a real load-generator run

Captured 2026-08-01 from an actual generator run against a live SIP endpoint.
Copied verbatim. No field was edited, renamed or synthesised.

Nothing here needed redacting: the files carry no hostname, IP, SIP URI or
credential. That was checked, not assumed.

## What it is

| File | What it proves |
|---|---|
| `gossipper_2958087_stats.log` | The stat file is named `gossipper_<pid>_stats.log` and is a **CSV by content with a `.log` extension**. 43 columns, 65 rows. Discovery that matches on `*.csv` never sees it. |
| `calls.jsonl` | The per-record schema, which was previously documented nowhere. 3 records. |
| `gossipper_2958087_errors.log` | One runtime error line, free-form. |
| `gossipper_2958087_error_codes.log` | Header only: `timestamp,call,code,reason,call_id,expected`. Zero rows, because no call got far enough to produce a SIP-level code. |

## Why the run failed, which is what makes it useful

Every call timed out waiting for a `200`, so `success_calls` is 0 and no media
flowed. That is not a defect in the fixture, it is the point: it exercises the
paths a clean run never reaches. `success_ratio` 0.0 is a real measurement and
must not read as absent, `rtp_packets_sent` 0 is a real zero and not a gap, and
the failure breakdown is entirely `timeout`.

**`summary.json` is absent**, because the run was killed before it finalised.
An import therefore has to succeed from the stat file alone, which is the
common case for any run that did not end cleanly.

## The header is the documented contract

Byte-for-byte identical to the header in the generator's own
`docs/trace-schema-contract.md`, and to the hand-built fixture derived from it
before any real run existed. The contract holds.
