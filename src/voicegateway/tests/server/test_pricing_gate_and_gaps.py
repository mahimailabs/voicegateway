"""The two API surfaces operator-declared pricing needs.

The design: an operator declares what they actually pay per model, the
catalogue only prefills the field, and a model is not offered to end users
until a rate exists for it. VoiceGateway ships no admin page for that. It
ships the mechanism, and every consumer UI asks it the same two questions.

**Can I offer this model?** ``serviceable`` on the quote endpoints. The trap
this pins is that ``serviceable`` is the single boolean, so it is what
everyone will gate on, and "a rate exists by any means" includes the
catalogue's list price. That is the number the whole design exists because it
is wrong. Under the default ``declared_only`` gate a catalogue price does NOT
make a model serviceable, so an operator who has declared nothing offers
nothing, rather than silently offering everything at list price.

**What do I still need to price?** ``/rate-card/gaps``. Someone has to type a
rate per model; this is the ranked list of which ones, so the job is finite.
"""

from __future__ import annotations

import time
import warnings

import pytest
import yaml
from fastapi.testclient import TestClient

from voicegateway.core.gateway import Gateway
from voicegateway.middleware.cost_tracker_middleware import CostTracker
from voicegateway.server.main import build_app

_CFG = {
    "providers": {},
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
}


def _client(tmp_path, monkeypatch, *, gate: str | None = None):
    warnings.filterwarnings("ignore")
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "gate.db"))
    cfg = dict(_CFG)
    if gate is not None:
        cfg = {**cfg, "pricing": {"gate": gate}}
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.safe_dump(cfg))
    gw = Gateway(config_path=str(path))
    return TestClient(build_app(gw, enable_dashboard=False)), gw


def _quote(client, modality: str, model: str, **params):
    r = client.get(
        "/v1/billing/rate-card/quote",
        params={"modality": modality, "model": model, **params},
    )
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_a_catalogue_price_alone_does_not_make_a_model_offerable(
    tmp_path, monkeypatch
) -> None:
    """The default, and the whole point of the design.

    deepgram/nova-3 has a published list price, so the catalogue can answer.
    That answer is wrong by an unknown margin for anyone on a negotiated
    contract, which is why the operator is asked to declare theirs. Treating
    the list price as sufficient would mean nobody ever has to.
    """
    client, _ = _client(tmp_path, monkeypatch)
    body = _quote(client, "stt", "deepgram/nova-3")
    assert body["catalog"]["priced"] is True
    assert body["priced_by"] == "catalog"
    assert body["serviceable"] is False
    assert body["gate"] == "declared_only"


def test_permissive_offers_the_catalogue_price_and_still_says_where_it_came_from(
    tmp_path, monkeypatch
) -> None:
    """The knowing opt-out. ``priced_by`` stays honest either way."""
    client, _ = _client(tmp_path, monkeypatch, gate="permissive")
    body = _quote(client, "stt", "deepgram/nova-3")
    assert body["serviceable"] is True
    assert body["priced_by"] == "catalog"
    assert body["gate"] == "permissive"


def test_an_unknown_model_is_never_offerable_under_either_gate(
    tmp_path, monkeypatch
) -> None:
    for gate in ("declared_only", "permissive"):
        client, _ = _client(tmp_path, monkeypatch, gate=gate)
        body = _quote(client, "stt", "nobody/made-this-up")
        assert body["serviceable"] is False, gate
        assert body["priced_by"] == "none", gate


def test_a_declared_rate_makes_a_model_offerable_under_the_strict_gate(
    tmp_path, monkeypatch
) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    r = client.post(
        "/v1/billing/rate-card/rules",
        json={
            "modality": "stt",
            "provider": "deepgram",
            "model": "nova-3",
            "fixed": 0.0055,
            "unit": "minute",
        },
    )
    assert r.status_code == 200, r.text
    body = _quote(client, "stt", "deepgram/nova-3")
    assert body["serviceable"] is True
    assert body["priced_by"] == "operator"
    assert body["effective"]["unit_price_usd"] == pytest.approx(0.0055)
    # The catalogue answer is still reported, so a UI can show the operator
    # what they are paying against the published rate.
    assert body["catalog"]["priced"] is True


def test_the_gate_is_echoed_so_a_policy_answer_is_not_read_as_a_fact(
    tmp_path, monkeypatch
) -> None:
    """``serviceable`` means different things under different config.

    A boolean whose meaning is configurable is one that gets misread by a
    consumer written against the other setting, so the policy travels with it.
    """
    strict, _ = _client(tmp_path, monkeypatch)
    assert _quote(strict, "stt", "deepgram/nova-3")["gate"] == "declared_only"


def test_the_bulk_quote_applies_the_same_gate(tmp_path, monkeypatch) -> None:
    """An editor prices a dropdown in one call; it must not get a laxer answer."""
    client, _ = _client(tmp_path, monkeypatch)
    r = client.post(
        "/v1/billing/rate-card/quote",
        json={
            "models": [
                {"modality": "stt", "model": "deepgram/nova-3"},
                {"modality": "stt", "model": "nobody/made-this-up"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    rows = r.json()["models"]
    assert [row["serviceable"] for row in rows] == [False, False]
    assert [row["priced_by"] for row in rows] == ["catalog", "none"]
    assert {row["gate"] for row in rows} == {"declared_only"}


# --------------------------------------------------------------------------
# The gap list
# --------------------------------------------------------------------------


async def _record(gw, model: str, modality: str = "stt", units: float = 5.0) -> None:
    tracker = CostTracker(storage=gw.storage)
    record = tracker.create_record(
        model_id=model,
        modality=modality,
        provider=model.split("/")[0],
        input_units=units,
    )
    await gw.storage.log_request(record)


@pytest.mark.asyncio
async def test_gaps_ranks_unpriceable_models_by_traffic(tmp_path, monkeypatch) -> None:
    client, gw = _client(tmp_path, monkeypatch)
    await gw.storage._ensure_initialized()
    for _ in range(3):
        await _record(gw, "rime/mistv2")
    await _record(gw, "lmnt/blizzard")
    await _record(gw, "deepgram/nova-3")  # priced, must not appear

    r = client.get("/v1/billing/rate-card/gaps")
    assert r.status_code == 200, r.text
    gaps = r.json()["gaps"]
    models = [g["model"] for g in gaps]
    assert models == ["rime/mistv2", "lmnt/blizzard"], models
    assert "deepgram/nova-3" not in models
    assert gaps[0]["requests"] == 3
    assert gaps[0]["unknown_requests"] == 3


@pytest.mark.asyncio
async def test_gaps_excludes_eou_rows_which_nobody_can_price(
    tmp_path, monkeypatch
) -> None:
    """End-of-utterance rows carry no model and an empty pricing_source.

    They look exactly like an unpriced request to a naive query, and there are
    a lot of them, so without the billable filter they dominate the list and
    the report tells the operator to go price a timing measurement.
    """
    client, gw = _client(tmp_path, monkeypatch)
    await gw.storage._ensure_initialized()
    tracker = CostTracker(storage=gw.storage)
    eou = tracker.create_record(
        model_id="", modality="eou", provider="", input_units=0.0
    )
    eou.timestamp = time.time()
    await gw.storage.log_request(eou)
    await _record(gw, "rime/mistv2")

    gaps = client.get("/v1/billing/rate-card/gaps").json()["gaps"]
    assert [g["model"] for g in gaps] == ["rime/mistv2"]


@pytest.mark.asyncio
async def test_gaps_reports_the_window_caveat_in_the_response(
    tmp_path, monkeypatch
) -> None:
    """A gap list that was ever silently short is one nobody trusts twice.

    Before ``voice-prices-unrated`` existed, a model that matched the catalogue
    with no rate was stamped as priced, so this report would have counted it as
    covered. A reader cannot tell that from the numbers, so the response says
    it rather than leaving it in a changelog.
    """
    client, _ = _client(tmp_path, monkeypatch)
    body = client.get("/v1/billing/rate-card/gaps").json()
    assert "under-report" in body["caveat_before"]


@pytest.mark.asyncio
async def test_gaps_is_empty_when_every_metered_model_has_a_rate(
    tmp_path, monkeypatch
) -> None:
    client, gw = _client(tmp_path, monkeypatch)
    await gw.storage._ensure_initialized()
    await _record(gw, "deepgram/nova-3")
    assert client.get("/v1/billing/rate-card/gaps").json()["gaps"] == []
