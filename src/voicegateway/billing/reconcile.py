"""Margin reconciliation + price sync for the rate card.

Two operator checks, kept as pure functions so the CLI stays thin:

* :func:`margin_reconcile` annotates rolled-up billing rows (rated revenue
  vs recorded cost) with a thin/negative flag, so a run that is losing money
  or barely making it is visible.
* :func:`sync_fixed_rules` checks each fixed ($/unit) rule against the
  current voice-prices base cost for one unit. Cost-plus rules auto-follow
  the base and need no sync; a fixed rule can silently cross into a thin or
  negative margin when the base moves, which this surfaces.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from voicegateway.billing.rate_card import RateCard, RateRule

# Default: flag any tenant/rule keeping under 20% of rated revenue as margin.
DEFAULT_THIN_PCT = 20.0


def _flag(margin: float, rated_or_price: float, thin_pct: float) -> str:
    """Classify a margin as negative, thin, or healthy ("")."""
    if margin < 0:
        return "negative"
    if rated_or_price > 0 and (margin / rated_or_price) * 100.0 < thin_pct:
        return "thin"
    return ""


@dataclass
class MarginLine:
    """One tenant's rated-vs-cost margin, flagged."""

    tenant_id: str | None
    requests: int
    cost_usd: float
    rated_usd: float
    margin_usd: float
    margin_pct: float
    flag: str  # "" | "thin" | "negative"


def margin_reconcile(
    usage_rows: list[dict],
    *,
    thin_pct: float = DEFAULT_THIN_PCT,
) -> list[MarginLine]:
    """Flag billing rollup rows whose margin is thin or negative.

    ``usage_rows`` is the output of ``get_billable_usage`` (each row carries
    ``margin_usd`` + ``margin_pct``).
    """
    lines: list[MarginLine] = []
    for r in usage_rows:
        lines.append(
            MarginLine(
                tenant_id=r["tenant_id"],
                requests=int(r["requests"]),
                cost_usd=float(r["cost_usd"]),
                rated_usd=float(r["rated_usd"]),
                margin_usd=float(r["margin_usd"]),
                margin_pct=float(r["margin_pct"]),
                flag=_flag(float(r["margin_usd"]), float(r["rated_usd"]), thin_pct),
            )
        )
    return lines


@dataclass
class SyncLine:
    """One fixed rule checked against the current base cost per unit."""

    rule: str  # audit token, e.g. "fixed:0.006/minute"
    scope: str  # e.g. "deepgram/nova-3 stt"
    unit: str
    fixed_price: float
    base_cost: float | None  # per-unit base cost, None if unresolvable
    margin_usd: float | None
    margin_pct: float | None
    flag: str  # "" | "thin" | "negative" | "unresolvable"


def _rule_scope(rule: RateRule) -> str:
    parts = [p for p in (rule.provider, rule.model) if p and p != "*"]
    label = "/".join(parts) if parts else "*"
    return f"{label} {rule.modality}".strip()


def sync_fixed_rules(
    card: RateCard,
    base_cost_per_unit: Callable[[RateRule], float | None],
    *,
    thin_pct: float = DEFAULT_THIN_PCT,
) -> list[SyncLine]:
    """Check every fixed rule's margin against the current base cost.

    ``base_cost_per_unit`` returns the current base cost for one ``unit`` of
    a rule, or ``None`` when it cannot be resolved (wildcard model, or a
    modality whose per-unit base is ambiguous, e.g. LLM token blends).
    """
    lines: list[SyncLine] = []
    for rule in card.rules:
        if rule.kind != "fixed":
            continue
        price = rule.unit_price_usd or 0.0
        base = base_cost_per_unit(rule)
        if base is None:
            lines.append(
                SyncLine(
                    rule=rule.describe(),
                    scope=_rule_scope(rule),
                    unit=str(rule.unit),
                    fixed_price=price,
                    base_cost=None,
                    margin_usd=None,
                    margin_pct=None,
                    flag="unresolvable",
                )
            )
            continue
        margin = price - base
        pct = (margin / price) * 100.0 if price > 0 else 0.0
        lines.append(
            SyncLine(
                rule=rule.describe(),
                scope=_rule_scope(rule),
                unit=str(rule.unit),
                fixed_price=price,
                base_cost=base,
                margin_usd=margin,
                margin_pct=pct,
                flag=_flag(margin, price, thin_pct),
            )
        )
    return lines


__all__ = [
    "DEFAULT_THIN_PCT",
    "MarginLine",
    "SyncLine",
    "margin_reconcile",
    "sync_fixed_rules",
]
