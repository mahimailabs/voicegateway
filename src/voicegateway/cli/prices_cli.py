"""``voicegw prices`` command group: inspect and reconcile the rate card.

Subcommands:

* ``ls``        - print the rate card in effect (default markup + rules).
* ``reconcile`` - roll up rated revenue vs recorded cost per tenant and flag
                  thin or negative margins.
* ``sync``      - check each fixed ($/unit) rule against the current
                  voice-prices base cost; cost-plus rules auto-follow.
"""

from __future__ import annotations

import typer
from rich.table import Table

from voicegateway.billing.rate_card import RateCard, RateRule
from voicegateway.billing.reconcile import margin_reconcile, sync_fixed_rules
from voicegateway.cli._app import app, console
from voicegateway.cli.base_cli import BaseCli
from voicegateway.inference.pricing import catalog
from voicegateway.utils.cli._shared import _parse_iso_date_arg

_cli = BaseCli()

prices_app = typer.Typer(
    name="prices",
    help="Inspect and reconcile the billing rate card.",
    no_args_is_help=True,
)
app.add_typer(prices_app, name="prices")


def _load_card(config: str | None) -> RateCard:
    gw = _cli.require_gateway(config)
    return RateCard.from_config(gw.config.rate_card)


@prices_app.command("ls")
def ls_cmd(
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to voicegw.yaml."
    ),
) -> None:
    """Print the rate card in effect."""
    card = _load_card(config)
    console.print(f"[bold]default markup[/bold]: {card.default_markup:g}")
    if not card.rules:
        console.print("No rate-card rules; every request bills at the default markup.")
        return
    table = Table(title="Rate card")
    for col in ("scope", "tenant", "plan", "kind", "price"):
        table.add_column(col)
    for rule in card.rules:
        table.add_row(
            _scope(rule),
            rule.tenant or "*",
            rule.plan or "*",
            rule.kind,
            rule.describe(),
        )
    console.print(table)


def _scope(rule: RateRule) -> str:
    parts = [p for p in (rule.provider, rule.model) if p and p != "*"]
    label = "/".join(parts) if parts else "*"
    return f"{label} ({rule.modality})"


@prices_app.command("reconcile")
def reconcile_cmd(
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to voicegw.yaml."
    ),
    period: str = typer.Option("month", "--period", help="today | week | month."),
    start: str | None = typer.Option(None, "--start", help="Start date YYYY-MM-DD."),
    end: str | None = typer.Option(None, "--end", help="End date YYYY-MM-DD."),
    threshold: float = typer.Option(
        20.0, "--threshold", help="Flag tenants under this margin %% as thin."
    ),
) -> None:
    """Roll up rated revenue vs cost per tenant; flag thin/negative margins."""
    gw = _cli.require_gateway(config)
    storage = _cli.require_storage(gw)
    start_ts = _parse_iso_date_arg(start, end_of_day=False) if start else None
    end_ts = _parse_iso_date_arg(end, end_of_day=True) if end else None
    rows = _cli.async_run(
        storage.get_billable_usage(period=period, start_ts=start_ts, end_ts=end_ts)
    )
    lines = margin_reconcile(rows, thin_pct=threshold)
    if not lines:
        console.print("No billable usage in the window.")
        return
    table = Table(title="Margin reconcile")
    for col in ("tenant", "requests", "cost", "rated", "margin", "margin %", "flag"):
        table.add_column(col)
    for ln in lines:
        table.add_row(
            ln.tenant_id or "(none)",
            str(ln.requests),
            f"${ln.cost_usd:.4f}",
            f"${ln.rated_usd:.4f}",
            f"${ln.margin_usd:.4f}",
            f"{ln.margin_pct:.1f}%",
            _mark(ln.flag),
        )
    console.print(table)


def _mark(flag: str) -> str:
    if flag == "negative":
        return "[red]NEGATIVE[/red]"
    if flag == "thin":
        return "[yellow]thin[/yellow]"
    if flag == "unresolvable":
        return "[dim]unresolvable[/dim]"
    return "ok"


def _base_cost_per_unit(rule: RateRule) -> float | None:
    """Current voice-prices base cost for one ``unit`` of a fixed rule.

    Resolvable only for a concrete model on a per-unit modality (STT
    minute/second, TTS char/1k_char); returns ``None`` otherwise.
    """
    if rule.model == "*":
        return None
    model_id = rule.model if "/" in rule.model else f"{rule.provider}/{rule.model}"
    if model_id.startswith("*/"):
        return None
    unit = rule.unit
    if unit in ("minute", "second"):
        seconds = 60.0 if unit == "minute" else 1.0
        cost = catalog.calculate_cost("stt", model_id, audio_seconds=seconds)
    elif unit in ("char", "1k_char"):
        chars = 1000 if unit == "1k_char" else 1
        cost = catalog.calculate_cost("tts", model_id, character_count=chars)
    else:
        return None  # token/request blends: no single per-unit base
    return float(cost) if cost is not None else None


@prices_app.command("sync")
def sync_cmd(
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to voicegw.yaml."
    ),
    threshold: float = typer.Option(
        20.0, "--threshold", help="Flag fixed rules under this margin %% as thin."
    ),
) -> None:
    """Check fixed rules against the current base cost (cost-plus auto-follows)."""
    card = _load_card(config)
    lines = sync_fixed_rules(card, _base_cost_per_unit, thin_pct=threshold)
    if not lines:
        console.print(
            "No fixed rules to sync; cost-plus rules auto-follow the base price."
        )
        return
    table = Table(title="Price sync (fixed rules)")
    for col in ("scope", "unit", "fixed", "base", "margin", "margin %", "flag"):
        table.add_column(col)
    for ln in lines:
        base = f"${ln.base_cost:.6f}" if ln.base_cost is not None else "-"
        margin = f"${ln.margin_usd:.6f}" if ln.margin_usd is not None else "-"
        pct = f"{ln.margin_pct:.1f}%" if ln.margin_pct is not None else "-"
        table.add_row(
            ln.scope,
            ln.unit,
            f"${ln.fixed_price:.6f}",
            base,
            margin,
            pct,
            _mark(ln.flag),
        )
    console.print(table)
