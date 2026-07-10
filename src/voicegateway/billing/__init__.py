"""Billing: rate card, write-time rating, and margin reconciliation.

VoiceGateway is the rating layer: it turns recorded provider cost into a
billable ``rated_price_usd`` using a configurable rate card. Downstream
systems (ShipVoice) own Stripe, invoices, and credits.
"""

from __future__ import annotations

from voicegateway.billing.rate_card import RateCard, RateRule
from voicegateway.billing.rating import RatedResult, price

__all__ = ["RateCard", "RateRule", "RatedResult", "price"]
