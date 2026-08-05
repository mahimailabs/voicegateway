"""Generate the two SYNTHETIC load fixtures beside this file.

Committed so the fixtures are reproducible and so their arithmetic is reviewable
rather than being 300 lines of opaque CSV. Run it from the repository root:

    .venv/bin/python src/voicegateway/tests/fixtures/loadtest/make_synthetic.py

These are SYNTHETIC. Every number is computed here, nothing was measured, and
that is stated in each directory's PROVENANCE.md. They exist because every real
artifact in this repository is of a failed run, so nothing had ever exercised
the path where a run passes. The one real capture stays the authority on SHAPE:
the header is copied from it byte for byte, and the file is named the way the
generator names it, ``gossipper_<pid>_stats.log``.

Two fixtures, because ONE RUN CANNOT BE BOTH.

``acceptance-500`` is an acceptance run: it holds its target inside every
threshold, so its verdict is PASS.

``saturation-ramp`` is a sizing run: it pushes a node past the 70% CPU ceiling
on purpose, because ``derive_calls_per_node`` refuses a ramp that never
saturated ("it carries AT LEAST N calls, which is a floor on its capacity and
not a measure of it").

EVERY STEP RUNS PAST :data:`capacity.MIN_STEADY_STATE_S`, and that is a property
of the fixture rather than an incidental length. A step shorter than that spends
most of its wall time establishing calls rather than holding them, so its CPU
reports a call-SETUP rate and the derivation refuses it. These steps used to be
sixty seconds, which made this fixture a sizing run that the code it exercises
would now decline to size. The step that makes the figure derivable is the same step
that fails the CPU gate, so this run's verdict is FAIL by construction. That is
not a defect in either the gate or the derivation. Measuring capacity means
exceeding the ceiling; passing acceptance means staying under it. They are two
runs and the report is right to describe them differently.
"""

from __future__ import annotations

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
HEADER = (
    (HERE / "capture-01" / "gossipper_2958087_stats.log").read_text().splitlines()[0]
)
COLUMNS = HEADER.split(",")

# 2026-08-02T09:00:00Z, a round instant so the windows are easy to reason about.
BASE_MS = 1_785_661_200_000


def _timestamp(at_ms: int) -> str:
    """RFC 3339 with nanoseconds, the way the generator writes it."""
    import datetime as dt

    moment = dt.datetime.fromtimestamp(at_ms / 1000, dt.UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond:06d}000Z"


def _row(**values: object) -> str:
    """One CSV row, with every column the generator emits and no other."""
    unknown = set(values) - set(COLUMNS)
    if unknown:
        raise SystemExit(f"not columns the generator emits: {sorted(unknown)}")
    return ",".join(str(values.get(name, 0)) for name in COLUMNS)


def _stat_file(
    path: pathlib.Path,
    *,
    start_ms: int,
    seconds: int,
    target: int,
    established: int,
    failed: int,
    ramp_seconds: int = 10,
) -> None:
    """One step: ramp to ``target`` concurrent, hold, then drain.

    Establishment is spread across the ramp so the cumulative counters climb the
    way a real run's do rather than stepping once at the end.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [HEADER]
    total = established + failed
    for second in range(1, seconds + 1):
        at_ms = start_ms + second * 1000
        # Concurrency: linear ramp, flat hold, linear drain over the last 5s.
        if second <= ramp_seconds:
            active = round(target * second / ramp_seconds)
        elif second > seconds - 5:
            active = round(target * (seconds - second) / 5)
        else:
            active = target
        # Cumulative call counts climb over the ramp and then stay put.
        done = min(1.0, second / ramp_seconds)
        placed = round(total * done)
        ok = round(established * done)
        bad = placed - ok
        lines.append(
            _row(
                timestamp=_timestamp(at_ms),
                elapsed_ms=second * 1000,
                total_calls=placed,
                success_calls=ok,
                failed_calls=bad,
                active_calls=active,
                success_ratio=f"{(ok / placed if placed else 0):.6f}",
                calls_per_second=f"{placed / second:.6f}",
                # 20ms Opus frames both ways for every call that connected.
                rtp_packets_sent=ok * 50 * second,
                rtp_packets_received=ok * 50 * second,
                failure_timeout=bad,
                interval_ms=1000,
                delta_total_calls=placed,
                delta_success_calls=ok,
                delta_failed_calls=bad,
                delta_failure_timeout=bad,
            )
        )
    path.write_text("\n".join(lines) + "\n")


def _call_records(path: pathlib.Path, *, established: int, failed: int) -> None:
    """One ``calls.jsonl`` record per call, in the shape ``capture-01`` carries.

    Needed because two-way media is a SEPARATE criterion from establishment and
    is judged per call. Without these records the gate reads UNKNOWN, and
    unmeasured is not a pass, so a fixture asserting the happy path has to
    demonstrate the audio came back rather than only that the calls connected.

    Every established call receives 14,997 of the 15,000 packets it sent. Not
    15,000: a lossless call is rarer than a counter that forgot to decrement,
    and the real capture shows the same three-packet shortfall. Failed calls
    carry zeroes both ways, which is what makes them not count as silent: they
    never answered, so the establishment gate already owns them.
    """
    sent = 15_000
    lines = []
    for number in range(1, established + failed + 1):
        ok = number <= established
        lines.append(
            json.dumps(
                {
                    "schema_version": "gossipper_call_record_v1",
                    "call_id": f"gossip-{number}-{number}-5f2a1c9d",
                    "call_number": number,
                    "success": ok,
                    "duration_ms": 300_000 if ok else 0,
                    "error": "" if ok else "timeout",
                    "media": {
                        "RTPPacketsSent": sent if ok else 0,
                        "RTPOctetsSent": sent * 160 if ok else 0,
                        "RTPPacketsReceived": sent - 3 if ok else 0,
                        "RTCPSenderReports": 599 if ok else 0,
                    },
                }
            )
        )
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    # ---- acceptance-500: one step, inside every threshold ------------------
    # 15000 attempted, 14985 established. 99.9%, above the 99.5% floor and not
    # a suspicious 100%: a run with no failures at all is rarer than a report
    # that forgot to count them.
    # Six minutes, not two: past MIN_STEADY_STATE_S, so the step is a statement
    # about the calls the node HELD rather than about the rate they arrived at.
    _stat_file(
        HERE / "acceptance-500" / "gossipper_4410_stats.log",
        start_ms=BASE_MS,
        seconds=360,
        target=500,
        established=14_985,
        failed=15,
    )
    _call_records(
        HERE / "acceptance-500" / "calls.jsonl", established=14_985, failed=15
    )

    # ---- saturation-ramp: four steps, the last one over the ceiling --------
    # Peak concurrency must rise with the target at every step, or
    # detect_plateau correctly refuses: "asked for more, got the same" means
    # something stopped scaling and the ceiling is not attributable.
    for index, target in enumerate((100, 150, 200, 250)):
        established = target * 30
        _stat_file(
            HERE
            / "saturation-ramp"
            / f"ramp-{target}"
            / f"gossipper_{5000 + index}_stats.log",
            # Steps run back to back, 390s apart, so their windows do not
            # overlap and each correlates to its own node samples. Six minutes
            # each, past MIN_STEADY_STATE_S: a sixty-second step measures how
            # fast calls arrived, not how many the node carried, and the
            # derivation declines to size a fleet from one.
            start_ms=BASE_MS + index * 390_000,
            seconds=360,
            target=target,
            established=established,
            failed=round(established * 0.001),
        )
        _call_records(
            HERE / "saturation-ramp" / f"ramp-{target}" / "calls.jsonl",
            established=established,
            failed=round(established * 0.001),
        )
    print("wrote acceptance-500 and saturation-ramp")


if __name__ == "__main__":
    main()
