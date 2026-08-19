"""Pin a known-good window, then fail CI when a later one drifts from it.

The percentiles and costs were readable and comparing two releases was manual:
read the numbers, remember the old ones, decide. Latency and cost regressions
are the ones that ship, because nothing fails when they do. A test suite catches
an agent that is broken; nothing catches an agent that got 300ms slower or 40%
more expensive per call, and the caller feels the first while the bill shows the
second.

Four rules shape everything here, and they are all the same rule: A COMPARISON
THAT CANNOT BE MADE MUST NOT READ AS A PASS.

* Tolerance is per metric. Cost and p95 do not move for the same reasons and do
  not deserve the same slack.
* Sample count is part of the comparison. Three calls against a baseline of two
  hundred is not a result.
* A metric missing from the new window is UNMEASURED and named, never a pass.
* The output says what moved and by how much. A bare non-zero exit sends
  somebody back to the dashboard to work out what happened, which is the work
  this was supposed to remove.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Bumped when the pinned file's shape changes incompatibly. A baseline pinned
#: by an older version is refused rather than silently compared against fields
#: that have moved underneath it.
BASELINE_VERSION = 2

#: Fractional drift allowed per metric before it counts as a regression.
#:
#: Deliberately NOT one global number. A p95 wanders with traffic mix and a few
#: slow calls move it; a per-call cost is arithmetic over a rate card and should
#: barely move at all, so the same 20% slack that is noise for one is a bill
#: nobody agreed to for the other.
#:
#: These are OURS, not contracted, and a caller can override any of them. They
#: are a starting point that fails loudly rather than a threshold anybody
#: measured.
DEFAULT_TOLERANCES: dict[str, float] = {
    "response_speed_p50_ms": 0.15,
    "response_speed_p95_ms": 0.20,
    "llm_ttft_p95_ms": 0.20,
    "tts_ttfb_p95_ms": 0.20,
    "cost_per_call_usd": 0.10,
}

#: Below this many samples a window cannot support a comparison. Ten is low
#: enough to let a small smoke run report, and high enough that one outlier
#: cannot carry a percentile on its own.
DEFAULT_MIN_SAMPLES = 10

# Exit codes. STABLE: CI depends on these, so they are API and not an
# implementation detail. Anything added later takes a new number rather than
# renumbering these.
EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_INSUFFICIENT = 2
#: The baseline and the window come from different stores, so no comparison was
#: attempted. Its own code rather than folding into EXIT_INSUFFICIENT: 2 says "I
#: compared what I could and one metric had nothing behind it", a fact about the
#: DATA, where this is a fact about what was pointed at. Reusing 2 would not be
#: absent information, it would be WRONG information: a code that already means
#: something specific, reported for a situation it does not describe.
#:
#: 4 is deliberately NOT taken here for "the collector was unreachable". Nothing
#: in this change can return it, and an exit code that cannot occur is a
#: documented promise the code does not keep. It arrives with the read path that
#: can raise it.
EXIT_SOURCE_MISMATCH = 3


@dataclass
class Finding:
    """One metric's verdict, carrying enough to act without the dashboard."""

    metric: str
    status: str  # "ok" | "drift" | "unmeasured" | "insufficient"
    baseline: float | None = None
    current: float | None = None
    delta_fraction: float | None = None
    tolerance: float | None = None
    baseline_samples: int | None = None
    current_samples: int | None = None
    note: str = ""

    def describe(self) -> str:
        """One line naming what moved and by how much."""
        if self.status == "unmeasured":
            return f"{self.metric}: UNMEASURED in the new window ({self.note})"
        if self.status == "insufficient":
            return (
                f"{self.metric}: INSUFFICIENT DATA "
                f"({self.current_samples} samples, needs {self.note})"
            )
        assert self.baseline is not None and self.current is not None
        pct = (self.delta_fraction or 0.0) * 100.0
        arrow = "up" if pct >= 0 else "down"
        tol = (self.tolerance or 0.0) * 100.0
        verdict = "DRIFT" if self.status == "drift" else "ok"
        return (
            f"{self.metric}: {verdict} {self.baseline:g} -> {self.current:g} "
            f"({arrow} {abs(pct):.1f}%, tolerance {tol:.0f}%)"
        )


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """Drift outranks insufficiency: a measured regression is the worse news.

        Reporting "insufficient" while a metric that DID have enough samples
        regressed would bury the finding the command exists to surface.
        """
        if any(f.status == "drift" for f in self.findings):
            return EXIT_DRIFT
        if any(f.status in ("unmeasured", "insufficient") for f in self.findings):
            return EXIT_INSUFFICIENT
        return EXIT_OK


class SourceMismatch(Exception):
    """A pinned baseline and the current window came from different stores."""


def describe_source(source: dict[str, Any] | None) -> str:
    """One phrase naming where a set of figures came from."""
    if not source:
        return "an unrecorded source"
    kind = source.get("kind") or "unknown"
    if kind == "collector":
        return f"the collector at {source.get('base_url') or 'an unnamed URL'}"
    return "the local store"


def check_source_matches(pinned: dict[str, Any], current: dict[str, Any]) -> None:
    """Raise unless the pinned figures and the new window share a store.

    REFUSED, NOT WARNED. A warning in CI output is read once and then never
    again, and this is the failure that looks most like success: a local-pinned
    baseline checked against a collector produces numbers, produces a verdict,
    and produces a green tick, while being a comparison that never happened.

    The whole value of a pinned baseline is that a comparison either happened or
    it did not. A third state that resembles the first is worse than an error,
    because an error stops the build and this would not.

    The PROJECT is part of the identity, not only the store. A collector
    aggregates many agents, so the same URL with a different project scope is a
    different population and comparing across it silently answers a question
    nobody asked.
    """
    want = pinned.get("source") or {}
    have = current.get("source") or {}
    for key in ("kind", "base_url", "project"):
        if want.get(key) != have.get(key):
            raise SourceMismatch(
                f"this baseline was pinned against {describe_source(want)} "
                f"(project {want.get('project')!r}) and the window was read from "
                f"{describe_source(have)} (project {have.get('project')!r}). "
                "That is not a comparison. Re-pin against the source you intend "
                "to check, rather than comparing two different populations."
            )


def compare(
    pinned: dict[str, Any],
    current: dict[str, Any],
    *,
    tolerances: dict[str, float] | None = None,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> Report:
    """Compare a freshly collected window against a pinned one.

    Only metrics present in the PINNED file are compared. A metric the new
    window grew is not a regression and saying so would make every added
    measurement a CI failure; a metric the new window LOST is unmeasured and is
    reported, because that is the case where something quietly stopped being
    collected.
    """
    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    report = Report()
    base_metrics = pinned.get("metrics", {})
    cur_metrics = current.get("metrics", {})

    for name in sorted(base_metrics):
        base = base_metrics[name]
        base_value = base.get("value")
        cur = cur_metrics.get(name)
        if cur is None or cur.get("value") is None:
            report.findings.append(
                Finding(
                    metric=name,
                    status="unmeasured",
                    baseline=base_value,
                    baseline_samples=base.get("samples"),
                    note="the new window produced no value",
                )
            )
            continue
        cur_samples = int(cur.get("samples") or 0)
        if cur_samples < min_samples:
            report.findings.append(
                Finding(
                    metric=name,
                    status="insufficient",
                    baseline=base_value,
                    current=cur.get("value"),
                    baseline_samples=base.get("samples"),
                    current_samples=cur_samples,
                    note=str(min_samples),
                )
            )
            continue
        if not base_value:
            # A pinned zero has no meaningful fractional drift: everything is an
            # infinite increase from zero. Reported rather than divided by.
            report.findings.append(
                Finding(
                    metric=name,
                    status="unmeasured",
                    baseline=base_value,
                    current=cur.get("value"),
                    note="the pinned value is zero, so a ratio says nothing",
                )
            )
            continue
        delta = (float(cur["value"]) - float(base_value)) / float(base_value)
        allowed = tol.get(name, 0.0)
        report.findings.append(
            Finding(
                metric=name,
                status="drift" if abs(delta) > allowed else "ok",
                baseline=float(base_value),
                current=float(cur["value"]),
                delta_fraction=delta,
                tolerance=allowed,
                baseline_samples=base.get("samples"),
                current_samples=cur_samples,
            )
        )
    return report


__all__ = [
    "BASELINE_VERSION",
    "EXIT_SOURCE_MISMATCH",
    "SourceMismatch",
    "check_source_matches",
    "describe_source",
    "DEFAULT_MIN_SAMPLES",
    "DEFAULT_TOLERANCES",
    "EXIT_DRIFT",
    "EXIT_INSUFFICIENT",
    "EXIT_OK",
    "Finding",
    "Report",
    "compare",
]
