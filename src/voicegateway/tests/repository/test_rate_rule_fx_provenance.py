"""A converted price carries how it was derived, or it is not auditable.

The feature these pin: an operator converts a foreign-currency rate once, at
set time, and the stored figure never moves afterwards. What makes that safe
rather than merely fixed is the provenance travelling with it. A price with no
record of its rate is indistinguishable from a guess three months later, which
is the failure mode the columns exist to prevent.
"""

from __future__ import annotations

import time

import pytest

from voicegateway.repository import managed_rate_rule_repository as repo
from voicegateway.repository.managed_rate_rule_repository import FxProvenance
from voicegateway.tests.server._telemetry_harness import _Harness


@pytest.fixture
async def db():
    """Session against a throwaway SQLite file with migrations applied."""
    harness = _Harness()
    try:
        async with harness.gateway.storage.session() as session:
            yield session
    finally:
        harness.cleanup()


def _inr(amount: str = "3.00", fetched_at: float | None = None) -> FxProvenance:
    return FxProvenance(
        source_currency="INR",
        source_amount=amount,
        fx_rate="95.513702",
        fx_source="https://open.er-api.com/v6/latest/USD",
        fx_fetched_at=time.time() if fetched_at is None else fetched_at,
    )


async def test_a_hand_set_rule_records_no_provenance(db):
    """NULL is the honest reading: no rate was involved."""
    await repo.upsert_rule(
        db, provider="cartesia", modality="tts", fixed=0.03, unit="1k_char"
    )
    assert await repo.list_fx_rules(db) == []


async def test_a_converted_rule_records_all_five_fields(db):
    await repo.upsert_rule(
        db,
        provider="sarvam",
        modality="tts",
        fixed=0.03140911,
        unit="1k_char",
        fx=_inr(),
    )

    rows = await repo.list_fx_rules(db)

    assert len(rows) == 1
    row = rows[0]
    assert row["source_currency"] == "INR"
    assert row["source_amount"] == "3.00"
    assert row["fx_rate"] == "95.513702"
    assert row["fx_source"].startswith("https://")
    assert row["fx_fetched_at"] > 0


async def test_the_published_figure_is_recoverable_from_what_we_stored(db):
    """The point of storing the rate: you can re-derive the vendor's number."""
    from decimal import Decimal

    await repo.upsert_rule(
        db,
        provider="sarvam",
        modality="tts",
        fixed=0.03140911,
        unit="1k_char",
        fx=_inr(),
    )

    row = (await repo.list_fx_rules(db))[0]
    rederived = Decimal(str(row["unit_price_usd"])) * Decimal(row["fx_rate"])

    assert rederived.quantize(Decimal("0.01")) == Decimal(row["source_amount"])


async def test_amount_and_rate_survive_as_text(db):
    """Stored as strings because they are evidence.

    A float would round 0.00000001 or re-render 95.513702 differently, and the
    number would stop matching the page it was read from.
    """
    await repo.upsert_rule(
        db,
        provider="sarvam",
        modality="tts",
        fixed=0.00000001,
        unit="char",
        fx=FxProvenance(
            source_currency="INR",
            source_amount="0.00000001",
            fx_rate="95.513702",
            fx_source="t",
            fx_fetched_at=time.time(),
        ),
    )

    row = (await repo.list_fx_rules(db))[0]

    assert row["source_amount"] == "0.00000001"
    assert row["fx_rate"] == "95.513702"


async def test_resetting_by_hand_clears_stale_provenance(db):
    """A rule re-set in USD must not keep a rate that no longer explains it.

    Otherwise the row claims a derivation that is no longer true, which is
    worse than claiming none: it reads as audited when it is not.
    """
    await repo.upsert_rule(
        db, provider="sarvam", modality="tts", fixed=0.031, unit="1k_char", fx=_inr()
    )
    assert len(await repo.list_fx_rules(db)) == 1

    await repo.upsert_rule(
        db, provider="sarvam", modality="tts", fixed=0.05, unit="1k_char"
    )

    assert await repo.list_fx_rules(db) == []


async def test_reconverting_replaces_the_old_rate(db):
    """A refresh updates the provenance rather than appending a second record."""
    await repo.upsert_rule(
        db,
        provider="sarvam",
        modality="tts",
        fixed=0.031,
        unit="1k_char",
        fx=_inr(fetched_at=time.time() - 200 * 86400),
    )
    await repo.upsert_rule(
        db, provider="sarvam", modality="tts", fixed=0.032, unit="1k_char", fx=_inr()
    )

    rows = await repo.list_fx_rules(db)

    assert len(rows) == 1
    assert (time.time() - rows[0]["fx_fetched_at"]) < 60


async def test_oldest_rate_sorts_first(db):
    """The staleness report leads with what most needs attention."""
    now = time.time()
    await repo.upsert_rule(
        db,
        provider="new",
        modality="tts",
        fixed=0.01,
        unit="1k_char",
        fx=_inr(fetched_at=now),
    )
    await repo.upsert_rule(
        db,
        provider="old",
        modality="tts",
        fixed=0.02,
        unit="1k_char",
        fx=_inr(fetched_at=now - 300 * 86400),
    )

    rows = await repo.list_fx_rules(db)

    assert len(rows) == 2
    assert "old" in rows[0]["rule_id"], rows[0]["rule_id"]
    assert "new" in rows[1]["rule_id"], rows[1]["rule_id"]


async def test_only_converted_rules_appear(db):
    """A mixed card reports only what was converted."""
    await repo.upsert_rule(
        db, provider="cartesia", modality="tts", fixed=0.03, unit="1k_char"
    )
    await repo.upsert_rule(
        db,
        provider="sarvam",
        modality="tts",
        fixed=0.031,
        unit="1k_char",
        fx=_inr(),
    )
    await repo.upsert_rule(db, provider="openai", modality="llm", markup=1.3)

    rows = await repo.list_fx_rules(db)

    assert len(rows) == 1
    assert "sarvam" in rows[0]["rule_id"]
