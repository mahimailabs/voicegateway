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
            price = _fmt(self.unit_price_usd)
            return f"fixed:{price}/{self.unit}"
        return f"cost_plus:{_fmt(self.markup)}"


def _fmt(value: float | None) -> str:
    """Format a float compactly (``1.30`` -> ``1.3``, ``0.0060`` -> ``0.006``)."""
    if value is None:
        return "0"
    return f"{value:g}"


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
        if "fixed" in raw and raw["fixed"] is not None:
            unit = raw.get("unit")
            if unit not in VALID_UNITS:
                raise ValueError(
                    f"fixed rate rule needs a valid unit "
                    f"(one of {sorted(VALID_UNITS)}), got {unit!r}"
                )
            return RateRule(
                modality=modality,
                provider=provider,
                model=model,
                tenant=tenant,
                plan=plan,
                kind="fixed",
                unit_price_usd=float(raw["fixed"]),
                unit=str(unit),
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


__all__ = ["WILDCARD", "VALID_UNITS", "RateRule", "RateCard"]
