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
        cost = float(row.get("cost_usd", 0))
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
    out: dict[str, dict[str, float]] = {}
    for r in records:
        model_id = r.get("model_id", "")
        if not model_id.startswith(prefix):
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
) -> list[ReconcileLine]:
    """Produce the per-model diff between VG's logs and the provider file.

    Each ReconcileLine carries both the VG side and the provider side
    plus the absolute and percent differences. Models present on
    only one side are still surfaced (with the missing-side fields at
    zero and `matched_in_*` flags reflecting which side has data).
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
                cost_diff_pct=_pct(cost_diff, prov["cost"]),
                matched_in_vg=model in vg_agg,
                matched_in_provider=model in provider_agg,
            )
        )
    return lines


def format_text(lines: list[ReconcileLine], provider: str) -> str:
    """Pretty text table for terminal display."""
    if not lines:
        return f"No models to reconcile for provider {provider}.\n"
    unit_label = {
        "openai": "tokens",
        "deepgram": "audio_s",
        "cartesia": "chars",
    }[provider]
    header = (
        f"{'Model':<35} {'VG ' + unit_label:>14} {'Provider ' + unit_label:>14} "
        f"{'Δ%':>7} {'VG cost':>10} {'Prov cost':>10} {'Δ$':>10} {'Δ%':>7}"
    )
    rows = [header, "-" * len(header)]
    for line in lines:
        flags = ""
        if not line.matched_in_vg:
            flags = " (vg-missing)"
        elif not line.matched_in_provider:
            flags = " (prov-missing)"
        rows.append(
            f"{line.model[:35]:<35} "
            f"{line.vg_units:>14.1f} "
            f"{line.provider_units:>14.1f} "
            f"{line.units_diff_pct:>+6.2f}% "
            f"${line.vg_cost:>9.4f} "
            f"${line.provider_cost:>9.4f} "
            f"${line.cost_diff_abs:>+9.4f} "
            f"{line.cost_diff_pct:>+6.2f}%{flags}"
        )
    return "\n".join(rows) + "\n"


def format_csv(lines: list[ReconcileLine]) -> str:
    """CSV with one row per model."""
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
    ])
    for line in lines:
        writer.writerow([
            line.model,
            f"{line.vg_units:.4f}", f"{line.provider_units:.4f}",
            f"{line.units_diff_abs:.4f}", f"{line.units_diff_pct:.4f}",
            f"{line.vg_cost:.6f}", f"{line.provider_cost:.6f}",
            f"{line.cost_diff_abs:.6f}", f"{line.cost_diff_pct:.4f}",
            line.matched_in_vg, line.matched_in_provider,
        ])
    return buf.getvalue()


def format_json(lines: list[ReconcileLine]) -> str:
    """JSON list of per-model diff records."""
    return json.dumps(
        [line.__dict__ for line in lines], indent=2, default=str
    ) + "\n"
