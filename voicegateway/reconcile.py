"""Cost reconciliation: compare VG's logged costs against a provider's usage export.

The reconcile command reads VG's per-request log records for a window,
aggregates them per-model, parses the operator's normalized
provider-usage file (per `docs/reference/reconcile-formats.md`), and
produces a per-model diff with absolute and percent differences.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Maps the VG-side input_units convention to the canonical unit name
# in the provider-usage file, per docs/reference/reconcile-formats.md.
# - openai: VG logs tokens (input_units, output_units); canonical
#   file's `input_tokens + output_tokens` is the diff target.
# - deepgram: VG logs minutes (legacy CostTracker convention); canonical
#   file's `audio_seconds` is the diff target. Conversion below.
# - cartesia: VG logs characters; canonical file's `characters` matches.
SUPPORTED_PROVIDERS = ("openai", "deepgram", "cartesia")

# Each canonical reconcile-input file is modality-specific (per
# docs/reference/reconcile-formats.md). Aggregating VG records that
# happen to share the provider prefix but a different modality (e.g.
# `openai/whisper-1` STT rows mixed into an OpenAI LLM reconcile) would
# corrupt the diff. The map below pins the canonical billing modality
# per provider so `aggregate_vg_records` can filter on it.
_PROVIDER_MODALITY = {
    "openai": "llm",
    "deepgram": "stt",
    "cartesia": "tts",
}


DEFAULT_DIFF_THRESHOLD_PCT = 5.0


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


def _pct(diff: float, base: float) -> float:
    """Percentage difference, treating a zero base as 0% to avoid div-by-zero."""
    if base == 0:
        return 0.0
    return (diff / base) * 100.0


def parse_provider_file(provider: str, path: Path) -> dict[str, dict[str, float]]:
    """Parse the provider's normalized usage file.

    Returns a dict keyed by the bare model id (no `provider/` prefix),
    with values `{"units": <float>, "cost": <float>, "n_requests": <int>}`.

    Format auto-detected from extension: `.csv` or `.json`. Schemas per
    `docs/reference/reconcile-formats.md`.
    """
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
) -> dict[str, dict[str, float]]:
    """Aggregate VG's per-request rows into per-model totals.

    Output mirrors `parse_provider_file`'s shape, with the units
    converted to match the canonical provider-file unit (e.g.,
    Deepgram VG-minutes go to seconds via *60).
    """
    prefix = f"{provider}/"
    expected_modality = _PROVIDER_MODALITY.get(provider)
    out: dict[str, dict[str, float]] = {}
    for r in records:
        model_id = r.get("model_id", "")
        if not model_id.startswith(prefix):
            continue
        # A provider can appear under multiple modalities in the same
        # window (e.g. `openai/whisper-1` STT rows alongside
        # `openai/gpt-4o-mini` LLM rows). The canonical reconcile-input
        # file is modality-specific per provider, so skip rows whose
        # modality does not match.
        if expected_modality and r.get("modality") != expected_modality:
            continue
        bare_model = model_id[len(prefix):]
        bucket = out.setdefault(
            bare_model, {"units": 0.0, "cost": 0.0, "n_requests": 0.0}
        )
        # Per-modality unit translation. STT VG-side units are minutes
        # (legacy CostTracker convention; see voicegateway/middleware
        # /cost_tracker.py), but the canonical Deepgram file is in
        # seconds. Convert at the boundary.
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
    """Produce the per-model diff between VG's logs and the provider file.

    Each ReconcileLine carries both the VG side and the provider side
    plus the absolute and percent differences. Models present on
    only one side are still surfaced (with the missing-side fields at
    zero and `matched_in_*` flags reflecting which side has data).

    Lines whose ``abs(cost_diff_pct)`` exceeds ``threshold_pct`` are
    flagged via ``ReconcileLine.flagged = True``. Default threshold
    is 5.0% per design §3.3 — informed by the v0.0.4 disclosure that
    LLM cost estimates can drift up to ~5%. Lines that lack data on
    either side are NOT flagged (the percent diff is undefined).
    """
    vg_agg = aggregate_vg_records(provider, vg_records)
    provider_agg = parse_provider_file(provider, provider_file)

    all_models = sorted(set(vg_agg) | set(provider_agg))
    lines: list[ReconcileLine] = []
    for model in all_models:
        vg = vg_agg.get(model, {"units": 0.0, "cost": 0.0})
        prov = provider_agg.get(model, {"units": 0.0, "cost": 0.0})
        units_diff = prov["units"] - vg["units"]
        cost_diff = prov["cost"] - vg["cost"]
        cost_diff_pct = _pct(cost_diff, prov["cost"])
        matched_both = (model in vg_agg) and (model in provider_agg)
        # Flag only when both sides have data; missing-side rows
        # produce undefined percent diffs (the cost on the missing
        # side is zero) and would otherwise always flag.
        flagged = matched_both and abs(cost_diff_pct) > threshold_pct
        lines.append(
            ReconcileLine(
                model=model,
                vg_units=vg["units"],
                provider_units=prov["units"],
                units_diff_abs=units_diff,
                units_diff_pct=_pct(units_diff, prov["units"]),
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


# ANSI sequences used when format_text is asked to colorize. Yellow
# for flagged rows (over the |Δ%| threshold). Plain ANSI rather than
# pulling in `rich` so the script keeps zero-extra-deps; the CLI
# sets colorize only when stdout is a TTY.
_ANSI_YELLOW = "\033[33m"
_ANSI_RESET = "\033[0m"


def format_text(
    lines: list[ReconcileLine],
    provider: str,
    *,
    colorize: bool = False,
) -> str:
    """Pretty text table for terminal display.

    Adds a Total row at the bottom summing vg_cost and provider_cost
    across rows where both sides matched (missing-side rows would
    skew totals with their $0 placeholder). When ``colorize`` is
    True, flagged rows are wrapped in ANSI yellow so they stand out
    in a TTY; plain text otherwise. The CLI sets colorize when
    stdout.isatty().
    """
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
            # Per design §3.3 / TODO 4.3 #3: name the missing-VG
            # case in plain language so the row's $0 VG cell is
            # not read as a real $0 charge.
            flags = " (no vg data)"
        elif not line.matched_in_provider:
            # Per design §3.3 / TODO 4.3 #2: render as "no provider
            # data" rather than just a "$0 reference" so users do
            # not mistake the empty provider cell for a real $0
            # charge. The cost cells still show $0.0000 but the
            # explicit suffix names the absence.
            flags = " (no provider data)"
        else:
            # Only sum rows where both sides have data; missing
            # sides would skew totals.
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

    # Total row: sums over rows where both sides matched. Pinned
    # below a dashes separator so it does not get visually merged
    # with per-model rows.
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
    return "\n".join(rows) + "\n"


def format_csv(lines: list[ReconcileLine]) -> str:
    """CSV with one row per model.

    Same field set as the text format minus the visual presentation:
    one machine-readable column per ReconcileLine attribute.
    Includes the ``flagged`` column (added in 4.3 #1) so downstream
    spreadsheets can filter on it without re-deriving the threshold
    comparison.
    """
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "model",
        "vg_units", "provider_units",
        "units_diff_abs", "units_diff_pct",
        "vg_cost_usd", "provider_cost_usd",
        "cost_diff_abs", "cost_diff_pct",
        "matched_in_vg", "matched_in_provider",
        "flagged",
    ])
    for line in lines:
        writer.writerow([
            line.model,
            f"{line.vg_units:.4f}", f"{line.provider_units:.4f}",
            f"{line.units_diff_abs:.4f}", f"{line.units_diff_pct:.4f}",
            f"{line.vg_cost:.6f}", f"{line.provider_cost:.6f}",
            f"{line.cost_diff_abs:.6f}", f"{line.cost_diff_pct:.4f}",
            line.matched_in_vg, line.matched_in_provider,
            line.flagged,
        ])
    return buf.getvalue()


def format_json(
    lines: list[ReconcileLine],
    *,
    provider: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> str:
    """JSON document with the design §2.2 schema.

    Shape: ``{provider, period: {start, end}, rows: [...],
    total: {vg_cost, provider_cost, diff_abs, diff_pct},
    flagged_count}``. Total sums vg_cost / provider_cost across rows
    where both sides matched (missing-side rows excluded so their
    $0 placeholders do not skew the total). flagged_count counts
    rows whose ``flagged=True``.

    ``provider``/``period_start``/``period_end`` are optional so the
    helper can be called from contexts that don't track them
    (existing tests, REPL exploration); when omitted they render as
    null in the output.
    """
    total_vg = sum(
        ln.vg_cost for ln in lines
        if ln.matched_in_vg and ln.matched_in_provider
    )
    total_provider = sum(
        ln.provider_cost for ln in lines
        if ln.matched_in_vg and ln.matched_in_provider
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
