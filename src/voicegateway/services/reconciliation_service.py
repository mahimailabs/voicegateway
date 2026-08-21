"""Cost reconciliation: compare VG's logged costs against a provider's usage export."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_PROVIDERS = ("openai", "deepgram", "cartesia")
_PROVIDER_MODALITY = {
    "openai": "llm",
    "deepgram": "stt",
    "cartesia": "tts",
}


DEFAULT_DIFF_THRESHOLD_PCT = 5.0

# Below this, the two sides are metering the same work and any cost gap is a
# RATE disagreement rather than a measurement one. Deliberately tighter than
# the cost threshold: unit counts are counts, so they should agree closely,
# and a rounding difference in billable seconds is the only reason they would
# not.
UNITS_AGREE_PCT = 1.0

# A rate-card rule produced the cost. Matches the ``pricing_source`` written
# by CostTracker when an operator-declared rate applies.
_RATE_CARD_PREFIX = "rate-card:"


@dataclass
class ReconcileLine:
    """One per-model row in the reconcile output."""

    model: str
    vg_units: float
    provider_units: float
    units_diff_abs: float
    units_diff_pct: float
    vg_cost: float
    provider_cost: float
    cost_diff_abs: float
    cost_diff_pct: float
    matched_in_vg: bool
    matched_in_provider: bool
    flagged: bool = False
    # WHAT PRODUCED VG'S NUMBER. A flagged row used to say only that the two
    # sides disagree, which leaves the operator to guess whether their own
    # rate is wrong, the catalogue's is, or the two are metering different
    # amounts of work. Those have different fixes and only one of them is in
    # the operator's hands.
    pricing_sources: tuple[str, ...] = ()
    # The per-unit rates the two sides imply. When the units agree, these are
    # what actually differ, and the invoice's rate is the number the operator
    # should be typing into the rule.
    vg_rate: float | None = None
    provider_rate: float | None = None
    cause: str = ""  # "" | "rate" | "units" | "coverage"


def _rate(cost: float, units: float) -> float | None:
    """Implied per-unit rate, or None when there are no units to divide by."""
    return cost / units if units else None


def _diagnose(
    *,
    units_diff_pct: float,
    matched_both: bool,
) -> str:
    """Name what a flagged row's disagreement is ABOUT.

    Three causes with three different fixes:

    ``coverage``
        One side has the model and the other does not. Nothing to compare;
        either VG never metered it or the invoice does not itemise it.

    ``units``
        The two sides disagree about how much work happened. That is a
        metering question (a dropped session, a different billable-duration
        rounding), and no rate change will close it.

    ``rate``
        The unit counts agree and the money does not, so the per-unit rate is
        wrong. This is the one an operator can fix directly, and with
        operator-declared pricing it is the likely one: a hand-typed rate has
        no other oracle than the invoice.
    """
    if not matched_both:
        return "coverage"
    if abs(units_diff_pct) > UNITS_AGREE_PCT:
        return "units"
    return "rate"


def _pct(diff: float, base: float) -> float:
    """Percentage difference, treating a zero base as 0% to avoid div-by-zero."""
    if base == 0:
        return 0.0
    return (diff / base) * 100.0


def parse_provider_file(provider: str, path: Path) -> dict[str, dict[str, float]]:
    """Parse the provider's normalized usage file."""
    if not path.exists():
        raise FileNotFoundError(f"provider usage file not found: {path}")

    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"unsupported provider: {provider!r}. Supported: {SUPPORTED_PROVIDERS}"
        )

    if path.suffix == ".json":
        rows = json.loads(path.read_text())
    elif path.suffix == ".csv":
        with path.open() as f:
            rows = list(csv.DictReader(f))
    else:
        raise ValueError(
            f"unrecognized provider-usage-file extension: {path.suffix!r}. "
            "Use .csv or .json."
        )

    out: dict[str, dict[str, float]] = {}
    for row in rows:
        model = str(row["model"])
        cost = float(row.get("cost_usd", 0) or 0)
        n_requests = int(float(row.get("n_requests", 0) or 0))

        if provider == "openai":
            units = float(row.get("input_tokens", 0) or 0) + float(
                row.get("output_tokens", 0) or 0
            )
        elif provider == "deepgram":
            units = float(row.get("audio_seconds", 0) or 0)
        else:  # cartesia
            units = float(row.get("characters", 0) or 0)

        out[model] = {
            "units": units,
            "cost": cost,
            "n_requests": float(n_requests),
        }
    return out


def aggregate_vg_records(
    provider: str, records: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Aggregate VG's per-request rows into per-model totals.

    Carries the distinct ``pricing_source`` values through, because when the
    invoice disagrees the first useful question is which authority produced
    VG's figure: a rate the operator typed, the catalogue, or nothing at all.
    """
    prefix = f"{provider}/"
    expected_modality = _PROVIDER_MODALITY.get(provider)
    # ``Any`` because a bucket holds float totals plus the set of distinct
    # pricing sources seen for the model, which is not a number.
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        model_id = r.get("model_id", "")
        if not model_id.startswith(prefix):
            continue
        if expected_modality and r.get("modality") != expected_modality:
            continue
        bare_model = model_id[len(prefix) :]
        bucket = out.setdefault(
            bare_model,
            {"units": 0.0, "cost": 0.0, "n_requests": 0.0, "sources": set()},
        )
        source = str(r.get("pricing_source") or "")
        if source:
            bucket["sources"].add(source)

        input_units = float(r.get("input_units", 0) or 0)
        output_units = float(r.get("output_units", 0) or 0)
        if provider == "deepgram":
            bucket["units"] += input_units * 60.0
        elif provider == "openai":
            bucket["units"] += input_units + output_units
        else:  # cartesia
            bucket["units"] += input_units
        bucket["cost"] += float(r.get("cost_usd", 0) or 0)
        bucket["n_requests"] += 1.0
    return out


def reconcile(
    provider: str,
    vg_records: list[dict[str, Any]],
    provider_file: Path,
    threshold_pct: float = DEFAULT_DIFF_THRESHOLD_PCT,
) -> list[ReconcileLine]:
    """Produce the per-model diff between VG's logs and the provider file."""
    vg_agg = aggregate_vg_records(provider, vg_records)
    provider_agg = parse_provider_file(provider, provider_file)

    all_models = sorted(set(vg_agg) | set(provider_agg))
    lines: list[ReconcileLine] = []
    for model in all_models:
        vg = vg_agg.get(model, {"units": 0.0, "cost": 0.0, "sources": set()})
        prov = provider_agg.get(model, {"units": 0.0, "cost": 0.0})
        units_diff = prov["units"] - vg["units"]
        cost_diff = prov["cost"] - vg["cost"]
        cost_diff_pct = _pct(cost_diff, prov["cost"])
        matched_both = (model in vg_agg) and (model in provider_agg)

        zero_base_real_diff = prov["cost"] == 0.0 and cost_diff != 0.0
        flagged = matched_both and (
            abs(cost_diff_pct) > threshold_pct or zero_base_real_diff
        )
        units_diff_pct = _pct(units_diff, prov["units"])
        lines.append(
            ReconcileLine(
                pricing_sources=tuple(sorted(vg.get("sources") or ())),
                vg_rate=_rate(vg["cost"], vg["units"]),
                provider_rate=_rate(prov["cost"], prov["units"]),
                cause=(
                    _diagnose(units_diff_pct=units_diff_pct, matched_both=matched_both)
                    if flagged or not matched_both
                    else ""
                ),
                model=model,
                vg_units=vg["units"],
                provider_units=prov["units"],
                units_diff_abs=units_diff,
                units_diff_pct=units_diff_pct,
                vg_cost=vg["cost"],
                provider_cost=prov["cost"],
                cost_diff_abs=cost_diff,
                cost_diff_pct=cost_diff_pct,
                matched_in_vg=model in vg_agg,
                matched_in_provider=model in provider_agg,
                flagged=flagged,
            )
        )
    return lines


_ANSI_YELLOW = "\033[33m"
_ANSI_RESET = "\033[0m"


def format_text(
    lines: list[ReconcileLine],
    provider: str,
    *,
    colorize: bool = False,
) -> str:
    """Pretty text table for terminal display."""
    if not lines:
        return f"No models to reconcile for provider {provider}.\n"
    unit_label = {
        "openai": "tokens",
        "deepgram": "audio_s",
        "cartesia": "chars",
    }.get(provider, "units")
    header = (
        f"{'Model':<35} {'VG ' + unit_label:>14} {'Provider ' + unit_label:>14} "
        f"{'Δ%':>7} {'VG cost':>10} {'Prov cost':>10} {'Δ$':>10} {'Δ%':>7}"
    )
    rows = [header, "-" * len(header)]
    total_vg_cost = 0.0
    total_provider_cost = 0.0
    flagged_count = 0
    for line in lines:
        flags = ""
        if not line.matched_in_vg:
            flags = " (no vg data)"
        elif not line.matched_in_provider:
            flags = " (no provider data)"
        else:
            total_vg_cost += line.vg_cost
            total_provider_cost += line.provider_cost
        if line.flagged:
            flagged_count += 1
            flags = flags + " *" if flags else " *"
        row_text = (
            f"{line.model[:35]:<35} "
            f"{line.vg_units:>14.1f} "
            f"{line.provider_units:>14.1f} "
            f"{line.units_diff_pct:>+6.2f}% "
            f"${line.vg_cost:>9.4f} "
            f"${line.provider_cost:>9.4f} "
            f"${line.cost_diff_abs:>+9.4f} "
            f"{line.cost_diff_pct:>+6.2f}%{flags}"
        )
        if colorize and line.flagged:
            row_text = f"{_ANSI_YELLOW}{row_text}{_ANSI_RESET}"
        rows.append(row_text)

    total_diff_abs = total_provider_cost - total_vg_cost
    total_diff_pct = _pct(total_diff_abs, total_provider_cost)
    rows.append("-" * len(header))
    rows.append(
        f"{'Total':<35} "
        f"{'':>14} {'':>14} "
        f"{'':>7} "
        f"${total_vg_cost:>9.4f} "
        f"${total_provider_cost:>9.4f} "
        f"${total_diff_abs:>+9.4f} "
        f"{total_diff_pct:>+6.2f}%"
    )
    if flagged_count:
        rows.append(f"  ({flagged_count} flagged row(s) marked with *)")
        rows.extend(explain(lines))
    return "\n".join(rows) + "\n"


def explain(lines: list[ReconcileLine]) -> list[str]:
    """Per-flagged-row guidance naming the suspect and the number to change.

    A diff on its own tells an operator that something is wrong, not what.
    That gap matters more now that rates are hand-entered: a rule typed as
    0.008 instead of 0.08 has no oracle inside the product, and the invoice is
    the only thing that can catch it. So when the unit counts agree, this says
    which rate produced VG's figure and what the invoice implies it should be,
    which is the edit rather than a hint toward it.
    """
    out: list[str] = ["", "What to check:"]
    for line in lines:
        if not line.flagged and line.matched_in_vg and line.matched_in_provider:
            continue
        if line.cause == "coverage":
            missing = "VG" if not line.matched_in_vg else "the provider export"
            out.append(
                f"  {line.model}: only one side has this model ({missing} has no rows)."
            )
            continue
        if line.cause == "units":
            out.append(
                f"  {line.model}: the unit counts disagree by "
                f"{line.units_diff_pct:+.2f}%, so this is a metering gap rather "
                f"than a rate. No rate change closes it."
            )
            continue
        # cause == "rate": the counts agree, so the per-unit rate is the fault.
        declared = [s for s in line.pricing_sources if s.startswith(_RATE_CARD_PREFIX)]
        if declared:
            rule = declared[0][len(_RATE_CARD_PREFIX) :]
            who = f"rate-card rule {rule!r}"
        elif line.pricing_sources:
            who = f"the catalog ({', '.join(line.pricing_sources)})"
        else:
            who = "nothing (these rows were never priced)"
        detail = (
            f"  {line.model}: units agree, cost does not. VG priced this from {who}."
        )
        if line.vg_rate is not None and line.provider_rate is not None:
            factor = (
                f" ({line.provider_rate / line.vg_rate:.2f}x)" if line.vg_rate else ""
            )
            detail += (
                f" Your rate implies ${line.vg_rate:.8f}/unit; the invoice implies "
                f"${line.provider_rate:.8f}/unit{factor}."
            )
        out.append(detail)
    return out


_EXTRA_FIELDS = ("cause", "pricing_sources", "vg_rate", "provider_rate")


def format_csv(lines: list[ReconcileLine]) -> str:
    """CSV with one row per model."""
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "model",
            "vg_units",
            "provider_units",
            "units_diff_abs",
            "units_diff_pct",
            "vg_cost_usd",
            "provider_cost_usd",
            "cost_diff_abs",
            "cost_diff_pct",
            "matched_in_vg",
            "matched_in_provider",
            "flagged",
            "cause",
            "pricing_sources",
            "vg_rate",
            "provider_rate",
        ]
    )
    for line in lines:
        writer.writerow(
            [
                line.model,
                f"{line.vg_units:.4f}",
                f"{line.provider_units:.4f}",
                f"{line.units_diff_abs:.4f}",
                f"{line.units_diff_pct:.4f}",
                f"{line.vg_cost:.6f}",
                f"{line.provider_cost:.6f}",
                f"{line.cost_diff_abs:.6f}",
                f"{line.cost_diff_pct:.4f}",
                line.matched_in_vg,
                line.matched_in_provider,
                line.flagged,
                line.cause,
                "|".join(line.pricing_sources),
                "" if line.vg_rate is None else f"{line.vg_rate:.10f}",
                "" if line.provider_rate is None else f"{line.provider_rate:.10f}",
            ]
        )
    return buf.getvalue()


def format_json(
    lines: list[ReconcileLine],
    *,
    provider: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> str:
    """JSON document with the design §2.2 schema."""
    total_vg = sum(
        ln.vg_cost for ln in lines if ln.matched_in_vg and ln.matched_in_provider
    )
    total_provider = sum(
        ln.provider_cost for ln in lines if ln.matched_in_vg and ln.matched_in_provider
    )
    total_diff_abs = total_provider - total_vg
    total_diff_pct = _pct(total_diff_abs, total_provider)
    flagged_count = sum(1 for ln in lines if ln.flagged)
    document = {
        "provider": provider,
        "period": {"start": period_start, "end": period_end},
        "rows": [line.__dict__ for line in lines],
        "total": {
            "vg_cost": total_vg,
            "provider_cost": total_provider,
            "diff_abs": total_diff_abs,
            "diff_pct": total_diff_pct,
        },
        "flagged_count": flagged_count,
    }
    return json.dumps(document, indent=2, default=str) + "\n"


__all__ = [
    "DEFAULT_DIFF_THRESHOLD_PCT",
    "ReconcileLine",
    "SUPPORTED_PROVIDERS",
    "aggregate_vg_records",
    "format_csv",
    "format_json",
    "format_text",
    "parse_provider_file",
    "reconcile",
]
