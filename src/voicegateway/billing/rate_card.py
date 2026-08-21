"""Rate card: the price book VoiceGateway rates recorded usage against.

A :class:`RateCard` is a list of :class:`RateRule` rows plus a global
``default_markup`` fallback. Each rule is scoped on two axes:

* **who** (``tenant``, ``plan``) - ``None`` means "any" (global),
* **what** (``modality``, ``provider``, ``model``) - ``"*"`` means "any".

Rating a request resolves the single most specific matching rule.
Specificity ranks tenant over plan over global, and model over provider
over modality-only. Among equally specific rules the last one wins, so a
DB override appended after the YAML seed takes precedence.

A rule is one of two kinds:

* ``cost_plus`` - multiply the recorded provider cost by ``markup``
  (auto-follows voice-prices base movement),
* ``fixed`` - multiply an advertised ``unit_price_usd`` by the request's
  billable quantity in ``unit`` (decoupled from base cost).

This module is pure data + resolution; the arithmetic lives in
:mod:`voicegateway.billing.rating`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

WILDCARD = "*"

# Fixed-price units this card understands. ``billable_quantity`` in the
# rating module maps each onto a request's recorded input/output units.
VALID_UNITS = frozenset(
    {
        "minute",
        "second",
        "char",
        "1k_char",
        "token",
        "1k_token",
        "1m_token",
        "request",
    }
)

# Token units price THREE legs, not one quantity, so a single
# ``unit_price_usd`` cannot express them. See ``TOKEN_UNITS`` handling in
# :mod:`voicegateway.billing.rating`.
TOKEN_UNITS = frozenset({"token", "1k_token", "1m_token"})

# Which units a modality can actually be billed in. ``input_units`` means
# minutes for stt, characters for tts and prompt tokens for llm, so a unit
# from the wrong row multiplies the right number by the wrong rate and
# reports it as money. ``request`` is a flat per-call price and belongs to
# every modality, so it is deliberately absent here.
UNITS_BY_MODALITY: dict[str, frozenset[str]] = {
    "stt": frozenset({"minute", "second"}),
    "tts": frozenset({"char", "1k_char"}),
    "llm": frozenset(TOKEN_UNITS),
}


def modality_for_unit(unit: str | None) -> str | None:
    """The one modality ``unit`` can belong to, or None if it is ambiguous.

    Every billing unit except ``request`` names exactly one modality, so a
    rule that gives a unit has already said which modality it is for. That
    lets a rule written without an explicit ``modality`` be resolved rather
    than rejected, which matters because ``{provider: deepgram, fixed: 0.006,
    unit: minute}`` is the obvious way to write an stt rule.
    """
    if unit is None or unit == "request":
        return None
    for modality, units in UNITS_BY_MODALITY.items():
        if unit in units:
            return modality
    return None


def unit_matches_modality(unit: str, modality: str) -> bool:
    """True if ``unit`` is a legal billing unit for ``modality``.

    ``request`` matches everything. An unknown modality matches nothing,
    which is the safe answer: it means the caller cannot vouch for the pair.
    """
    if unit == "request":
        return True
    return unit in UNITS_BY_MODALITY.get(modality, frozenset())


def _model_matches(rule_model: str, model_id: str) -> bool:
    """True if ``rule_model`` matches a request's ``model_id``.

    Rules may name a model either bare (``nova-3``) or fully qualified
    (``deepgram/nova-3``); both forms match a request whose ``model_id``
    is the fully qualified string.
    """
    if rule_model == WILDCARD:
        return True
    if rule_model == model_id:
        return True
    bare = model_id.split("/", 1)[-1]
    return rule_model == bare


def validate_fixed_pricing(
    *,
    modality: str,
    unit: str | None,
    fixed: float | None,
    input_price_usd: float | None = None,
    cached_input_price_usd: float | None = None,
    output_price_usd: float | None = None,
) -> None:
    """Raise ``ValueError`` unless a fixed rule's pricing fields are coherent.

    Shared by the YAML seed parser and the DB/API upsert so the two cannot
    drift into accepting different rules. Every failure here is a deploy-time
    error on purpose: the alternative is a rule that rates every request at a
    plausible wrong number, which surfaces in a bill instead.
    """
    if unit not in VALID_UNITS:
        raise ValueError(
            f"a fixed rule needs a valid unit (one of {sorted(VALID_UNITS)}), "
            f"got {unit!r}"
        )

    # A unit names its own modality, so a wildcard modality is inferred rather
    # than rejected (see :func:`modality_for_unit`). A modality that is stated
    # AND contradicts the unit is a real error: it would bill one modality's
    # units at another's rate.
    if unit != "request" and modality != WILDCARD:
        if not unit_matches_modality(unit, modality):
            allowed = sorted(UNITS_BY_MODALITY.get(modality, frozenset())) + ["request"]
            raise ValueError(
                f"unit {unit!r} is not billable for modality {modality!r}; "
                f"valid units are {allowed}"
            )

    legs = (input_price_usd, cached_input_price_usd, output_price_usd)
    if unit in TOKEN_UNITS:
        if fixed is not None:
            raise ValueError(
                f"unit {unit!r} bills input and output at different rates, so "
                f"a single 'fixed' price cannot express it; set "
                f"input_price_usd and output_price_usd instead "
                f"(cached_input_price_usd is optional and defaults to input)"
            )
        if input_price_usd is None or output_price_usd is None:
            raise ValueError(
                f"a fixed rule priced per {unit!r} needs both input_price_usd "
                f"and output_price_usd"
            )
        return

    if fixed is None:
        raise ValueError(f"a fixed rule priced per {unit!r} needs a 'fixed' price")
    if any(leg is not None for leg in legs):
        raise ValueError(
            f"input/cached/output prices only apply to token units "
            f"{sorted(TOKEN_UNITS)}; unit {unit!r} takes a single 'fixed' price"
        )


@dataclass(frozen=True)
class RateRule:
    """A single rate-card row.

    Scope fields default to "any". ``kind`` selects the arithmetic:
    ``cost_plus`` uses ``markup``; ``fixed`` uses ``unit_price_usd`` +
    ``unit``.
    """

    modality: str = WILDCARD
    provider: str = WILDCARD
    model: str = WILDCARD
    tenant: str | None = None
    plan: str | None = None
    kind: str = "cost_plus"
    markup: float | None = None
    unit_price_usd: float | None = None
    unit: str | None = None
    # LLM legs. Input and output bill at different rates on every provider, so
    # a token-unit rule carries a rate per leg instead of one
    # ``unit_price_usd``. ``cached_input_price_usd`` is OPTIONAL and defaults
    # to the input rate, which is the correct default for an operator with no
    # negotiated cache discount.
    input_price_usd: float | None = None
    cached_input_price_usd: float | None = None
    output_price_usd: float | None = None

    def __post_init__(self) -> None:
        """Pin a fixed rule's modality from its unit when it was left wildcard.

        Done here rather than in each caller so the YAML seed, a DB row and a
        directly-constructed rule cannot disagree. Only fills a wildcard: a
        stated modality is left alone, and a contradiction between the two is
        rejected by :func:`validate_fixed_pricing` at load.
        """
        if self.kind != "fixed" or self.modality != WILDCARD:
            return
        inferred = modality_for_unit(self.unit)
        if inferred is not None:
            object.__setattr__(self, "modality", inferred)

    def cached_rate(self) -> float:
        """The rate for cached prompt tokens, defaulting to the input rate.

        A contract without a prompt-cache discount bills cached input at the
        ordinary input rate, so an omitted ``cached_input_price_usd`` must not
        read as free.
        """
        if self.cached_input_price_usd is not None:
            return self.cached_input_price_usd
        return self.input_price_usd or 0.0

    def matches(
        self,
        *,
        modality: str,
        provider: str,
        model_id: str,
        tenant: str | None,
        plan: str | None,
    ) -> bool:
        """True if this rule is a candidate for the given request."""
        if self.tenant is not None and self.tenant != tenant:
            return False
        if self.plan is not None and self.plan != plan:
            return False
        if self.modality != WILDCARD and self.modality != modality:
            return False
        if self.provider != WILDCARD and self.provider != provider:
            return False
        return _model_matches(self.model, model_id)

    def specificity(self) -> int:
        """Higher means more specific. Tenant > plan > model > provider > modality."""
        score = 0
        if self.tenant is not None:
            score += 1000
        if self.plan is not None:
            score += 100
        if self.model != WILDCARD:
            score += 10
        if self.provider != WILDCARD:
            score += 5
        if self.modality != WILDCARD:
            score += 1
        return score

    def describe(self) -> str:
        """Auditable one-token summary stamped onto each rated request."""
        if self.kind == "fixed":
            if self.unit in TOKEN_UNITS:
                legs = (
                    f"in={_fmt(self.input_price_usd)},"
                    f"cached={_fmt(self.cached_rate())},"
                    f"out={_fmt(self.output_price_usd)}"
                )
                return f"fixed:{legs}/{self.unit}"
            price = _fmt(self.unit_price_usd)
            return f"fixed:{price}/{self.unit}"
        return f"cost_plus:{_fmt(self.markup)}"


def _fmt(value: float | None) -> str:
    """Format a float compactly (``1.30`` -> ``1.3``, ``0.0060`` -> ``0.006``)."""
    if value is None:
        return "0"
    return f"{value:g}"


def rate_rule_from_row(row: dict) -> RateRule:
    """Build a :class:`RateRule` from a managed_rate_rules DB row dict.

    The DB row's fields map 1:1 to :class:`RateRule`, so this is a direct lift
    (used when merging DB overrides onto the YAML seed).
    """
    return RateRule(
        modality=row.get("modality", WILDCARD),
        provider=row.get("provider", WILDCARD),
        model=row.get("model", WILDCARD),
        tenant=row.get("tenant"),
        plan=row.get("plan"),
        kind=row.get("kind", "cost_plus"),
        markup=row.get("markup"),
        unit_price_usd=row.get("unit_price_usd"),
        unit=row.get("unit"),
        input_price_usd=row.get("input_price_usd"),
        cached_input_price_usd=row.get("cached_input_price_usd"),
        output_price_usd=row.get("output_price_usd"),
    )


@dataclass
class RateCard:
    """An ordered list of rules plus a global default markup fallback."""

    rules: list[RateRule] = field(default_factory=list)
    default_markup: float = 1.0

    @classmethod
    def from_config(cls, data: dict | None) -> RateCard:
        """Build a card from a ``rate_card:`` config mapping.

        Shape::

            default_markup: 1.30
            rules:
              - {provider: openai, markup: 1.5}
              - {modality: stt, provider: deepgram, model: nova-3,
                 fixed: 0.0060, unit: minute}
              - {tenant: acme, markup: 1.1}

        A row with ``fixed`` becomes a ``fixed`` rule (``unit`` required);
        otherwise it is ``cost_plus`` using ``markup`` (defaulting to the
        card's ``default_markup``).
        """
        if not data:
            return cls()
        default_markup = float(data.get("default_markup", 1.0))
        rules: list[RateRule] = []
        for raw in data.get("rules", []) or []:
            rules.append(cls._parse_rule(raw, default_markup))
        return cls(rules=rules, default_markup=default_markup)

    @staticmethod
    def _parse_rule(raw: dict, default_markup: float) -> RateRule:
        modality = str(raw.get("modality", WILDCARD))
        provider = str(raw.get("provider", WILDCARD))
        model = str(raw.get("model", WILDCARD))
        tenant = raw.get("tenant")
        plan = raw.get("plan")
        fixed = raw.get("fixed")
        legs = {
            "input_price_usd": raw.get("input_price_usd"),
            "cached_input_price_usd": raw.get("cached_input_price_usd"),
            "output_price_usd": raw.get("output_price_usd"),
        }
        if fixed is not None or any(v is not None for v in legs.values()):
            unit = raw.get("unit")
            validate_fixed_pricing(
                modality=modality,
                unit=unit,
                fixed=None if fixed is None else float(fixed),
                **{k: (None if v is None else float(v)) for k, v in legs.items()},
            )
            return RateRule(
                modality=modality,
                provider=provider,
                model=model,
                tenant=tenant,
                plan=plan,
                kind="fixed",
                unit_price_usd=None if fixed is None else float(fixed),
                unit=str(unit),
                **{k: (None if v is None else float(v)) for k, v in legs.items()},
            )
        markup = float(raw.get("markup", default_markup))
        return RateRule(
            modality=modality,
            provider=provider,
            model=model,
            tenant=tenant,
            plan=plan,
            kind="cost_plus",
            markup=markup,
        )

    def with_overrides(self, rows: list[dict]) -> RateCard:
        """Return a new card with DB override rules appended after the seed.

        Overrides go last so a DB rule wins a specificity tie against a seed
        rule at the same scope (matching :meth:`resolve`'s later-wins rule).
        """
        return RateCard(
            rules=[*self.rules, *(rate_rule_from_row(r) for r in rows)],
            default_markup=self.default_markup,
        )

    def resolve(
        self,
        *,
        modality: str,
        provider: str,
        model_id: str,
        tenant: str | None = None,
        plan: str | None = None,
    ) -> RateRule | None:
        """Return the most specific matching rule, or ``None`` if none match.

        Ties on specificity are broken in favour of the rule that appears
        later in :attr:`rules` (DB overrides layered after the seed).
        """
        best: RateRule | None = None
        best_score = -1
        for rule in self.rules:
            if not rule.matches(
                modality=modality,
                provider=provider,
                model_id=model_id,
                tenant=tenant,
                plan=plan,
            ):
                continue
            score = rule.specificity()
            if score >= best_score:
                best = rule
                best_score = score
        return best


__all__ = [
    "WILDCARD",
    "VALID_UNITS",
    "TOKEN_UNITS",
    "UNITS_BY_MODALITY",
    "unit_matches_modality",
    "modality_for_unit",
    "validate_fixed_pricing",
    "RateRule",
    "RateCard",
    "rate_rule_from_row",
]
