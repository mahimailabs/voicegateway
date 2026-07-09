"""``voicegateway.guard()``: the active control wrapper (framework-neutral).

``guard()`` is the *control* seam that composes with ``attach()`` (the *observe*
seam). Where ``attach()`` passively meters cost + latency, ``guard()`` actively
wraps a native provider to add:

- **fallback**: on a primary-provider error, try each fallback provider in order.
  Before the fallback runs, guard sets a ContextVar so ``attach()`` stamps
  ``fallback_from=<primary>`` and ``status="fallback"`` on the record it writes
  for the provider that actually ran.
- **rate_limit**: a token bucket parsed from a DSL string (``"60/min"``,
  ``"5/s"``) via the core rate-limiter.
- **budget**: query the core's accumulated spend for the window and block (raise)
  when over, parsed from a DSL string (``"$5.00/day"``, ``"$100/month"``).

``guard()`` writes **no** metrics itself: ``attach()`` is the sole meter, so
``guard(provider)`` + ``attach(session)`` never double-counts.

This module is framework-neutral: importing it (and the neutral ``guard``
dispatcher) imports **no** framework. The dispatcher detects the provider's
framework by module string and lazily imports the matching implementation only
on first use, so ``import voicegateway`` stays pure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from voicegateway._frameworks import detect_framework, require_extra

# --- DSL parsing -----------------------------------------------------------

# "N/min", "N/s", "N/sec", "N/minute". Whitespace tolerant.
_RATE_RE = re.compile(
    r"^\s*(?P<count>\d+(?:\.\d+)?)\s*/\s*(?P<unit>s|sec|second|m|min|minute)\s*$",
    re.IGNORECASE,
)

# "$X/day", "X/day", "$X/month". The leading $ is optional; whitespace tolerant.
_BUDGET_RE = re.compile(
    r"^\s*\$?\s*(?P<amount>\d+(?:\.\d+)?)\s*/\s*(?P<window>day|month)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RateLimitSpec:
    """A parsed rate limit: ``requests`` per ``per_seconds`` window."""

    requests: int
    per_seconds: float

    @property
    def requests_per_minute(self) -> int:
        """Requests normalized to a per-minute figure for the core limiter."""
        rpm = self.requests * (60.0 / self.per_seconds)
        # The core RateLimiter counts whole requests per minute; round to the
        # nearest integer, but never below 1 for a positive spec.
        return max(1, round(rpm))


@dataclass(frozen=True)
class BudgetSpec:
    """A parsed spend cap: ``amount_usd`` per ``window`` ("day" | "month")."""

    amount_usd: float
    window: str  # "day" | "month"

    @property
    def period(self) -> str:
        """Map the window onto the cost-repository period vocabulary."""
        # The cost repository speaks "today"/"week"/"month"; a daily cap maps to
        # "today" (spend since local midnight).
        return "today" if self.window == "day" else "month"


def parse_rate_limit(spec: str) -> RateLimitSpec:
    """Parse a rate-limit DSL string (``"60/min"``, ``"5/s"``) -> RateLimitSpec."""
    match = _RATE_RE.match(spec)
    if match is None:
        raise ValueError(
            f"invalid rate_limit {spec!r}; expected e.g. '60/min' or '5/s'"
        )
    count = float(match.group("count"))
    unit = match.group("unit").lower()
    per_seconds = 1.0 if unit in ("s", "sec", "second") else 60.0
    requests = int(count)
    if requests <= 0:
        raise ValueError(f"rate_limit {spec!r} must be a positive request count")
    return RateLimitSpec(requests=requests, per_seconds=per_seconds)


def parse_budget(spec: str) -> BudgetSpec:
    """Parse a budget DSL string (``"$5.00/day"``, ``"100/month"``) -> BudgetSpec."""
    match = _BUDGET_RE.match(spec)
    if match is None:
        raise ValueError(
            f"invalid budget {spec!r}; expected e.g. '$5.00/day' or '$100/month'"
        )
    amount = float(match.group("amount"))
    window = match.group("window").lower()
    if amount < 0:
        raise ValueError(f"budget {spec!r} must be a non-negative amount")
    return BudgetSpec(amount_usd=amount, window=window)


# --- neutral dispatcher ----------------------------------------------------


def guard(
    provider: Any,
    *,
    fallback: Any = (),
    rate_limit: str | None = None,
    budget: str | None = None,
    project: str | None = None,
) -> Any:
    """Wrap a native provider with control (fallback / rate-limit / budget).

    Returns a drop-in wrapper of the SAME framework type as ``provider`` (e.g. a
    ``livekit.agents.llm.LLM`` subclass for a LiveKit LLM plugin), so it slots
    into an ``AgentSession`` unchanged. The wrapper writes no metrics; pair it
    with ``attach(session)`` for cost + latency.

    Args:
        provider: a native framework provider (a LiveKit plugin here).
        fallback: an ordered list of same-framework providers to try, in order,
            when the primary raises. attach stamps ``fallback_from`` on the row
            for whichever provider actually produced the result.
        rate_limit: a DSL string like ``"60/min"`` or ``"5/s"``. When the bucket
            is empty the guarded call raises ``RateLimitExceeded``.
        budget: a DSL string like ``"$5.00/day"`` or ``"$100/month"``. When the
            window's accumulated spend is at/over the cap the call raises
            ``BudgetExceededError``.
        project: project id used for budget lookups + record tagging. Defaults to
            the active project.

    Raises:
        ImportError: when the provider's framework extra is not installed.
        ValueError: when ``rate_limit`` / ``budget`` cannot be parsed, or the
            provider's framework is not recognized.
    """
    framework = detect_framework(provider)
    if framework == "livekit":
        require_extra("livekit")
        # Lazy: importing the LiveKit impl pulls livekit.agents. Keeping it out
        # of module scope is what preserves ``import voicegateway`` purity.
        from voicegateway.inference.livekit.guard_livekit import guard_livekit

        return guard_livekit(
            provider,
            fallback=fallback,
            rate_limit=rate_limit,
            budget=budget,
            project=project,
        )
    if framework == "pipecat":
        require_extra("pipecat")
        # Lazy: importing the Pipecat impl pulls pipecat. Keeping it out of
        # module scope is what preserves ``import voicegateway`` purity.
        from voicegateway.inference.pipecat.guard_pipecat import guard_pipecat

        return guard_pipecat(
            provider,
            fallback=fallback,
            rate_limit=rate_limit,
            budget=budget,
            project=project,
        )
    raise ValueError(
        "voicegateway.guard() could not detect the framework of "
        f"{type(provider).__name__!r}. Pass a native LiveKit provider "
        "(e.g. a livekit.plugins.* STT/LLM/TTS instance)."
    )


__all__ = [
    "BudgetSpec",
    "RateLimitSpec",
    "guard",
    "parse_budget",
    "parse_rate_limit",
]
