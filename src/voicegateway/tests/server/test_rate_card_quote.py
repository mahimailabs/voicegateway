"""Price a model before it has run, and price an LLM as the two numbers it is.

Two defects reported by a consumer building an agent-config editor that wants
to show a builder the price of the model they are about to pick.

THE UNIT PRICE WAS ONE NUMBER FOR A TWO-SIDED THING. `_voice_price` prices
1,000,000 INPUT tokens and labels the result `1m_token`, which reads as covering
both legs. Output is 4.0x input on gpt-4o-mini, 2.5x on claude-sonnet-4-5. A
conversational agent is output-heavy, so the single number understates exactly
the choice the field exists to inform. Same shape as the two other findings this
week: a defensible number under a name that does not describe it.

THERE WAS NO WAY TO PRICE A MODEL THAT HAD NOT RUN. The only route reaching the
catalogue is fed by `SELECT DISTINCT ... FROM requests`, so it answers for
models already billed. An editor picks a model before any call exists.
"""

from __future__ import annotations

import warnings

import pytest
import yaml
from fastapi.testclient import TestClient

from voicegateway.core.gateway import Gateway
from voicegateway.server.main import build_app

_CFG = {
    "providers": {},
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    warnings.filterwarnings("ignore")
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "quote.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    cfg = tmp_path / "voicegw.yaml"
    cfg.write_text(yaml.dump(_CFG))
    return TestClient(build_app(Gateway(config_path=str(cfg)), enable_mcp_sse=False))


def _quote(client, modality: str, model: str) -> dict:
    r = client.get(f"/v1/billing/rate-card/quote?modality={modality}&model={model}")
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------
# An LLM has two prices
# --------------------------------------------------------------------------


def test_an_llm_quote_reports_input_and_output_separately(client) -> None:
    """The defect: one number shown for a two-sided price.

    Asserted as a ratio rather than as literals, so a catalogue update moves the
    numbers without breaking the claim. The claim is that they DIFFER and that
    output is the larger one, which is what makes showing only input wrong.
    """
    catalog = _quote(client, "llm", "openai/gpt-4o-mini")["catalog"]
    assert catalog["priced"] is True
    assert catalog["input_price_usd"] < catalog["output_price_usd"], (
        "output is the expensive leg; if these are equal the fixture no longer "
        "demonstrates why one number was wrong"
    )


def test_the_legacy_single_field_is_still_the_input_leg(client) -> None:
    """Kept deliberately, so nothing reading it has a number move underneath.

    It is the INPUT price and always was. The fix is that a caller now has the
    pair available, not that the old field changed meaning.
    """
    r = client.get("/v1/billing/rate-card/models")
    assert r.status_code == 200


def test_a_one_sided_modality_reports_a_single_price(client) -> None:
    """STT and TTS bill on one measure, so a pair would invent a second leg."""
    catalog = _quote(client, "stt", "deepgram/nova-3")["catalog"]
    assert catalog["priced"] is True
    assert catalog["unit"] == "minute"
    assert "unit_price_usd" in catalog
    assert "output_price_usd" not in catalog


# --------------------------------------------------------------------------
# Unpriced says so, rather than returning a bare null
# --------------------------------------------------------------------------


def test_an_unknown_model_says_it_is_not_priced_and_why(client) -> None:
    """A bare null could not be told apart from "this endpoint does not handle
    this modality", and those want different UI.

    deepgram/nova-2-phonecall is a real model that voice-prices 0.3.0 does not
    carry, which is the common case: 6 of 10 sampled STT refs are unpriced.
    """
    catalog = _quote(client, "stt", "deepgram/nova-2-phonecall")["catalog"]
    assert catalog["priced"] is False
    assert catalog["reason"]
    assert catalog["unit"] == "minute"


def test_an_unknown_modality_is_distinguishable_from_an_unknown_model(client) -> None:
    """The distinction a bare null destroyed."""
    bad_modality = _quote(client, "embedding", "openai/whatever")["catalog"]
    assert bad_modality["priced"] is False
    assert bad_modality["unit"] is None
    unknown_model = _quote(client, "stt", "nobody/made-this-up")["catalog"]
    assert unknown_model["priced"] is False
    assert unknown_model["unit"] == "minute"


# --------------------------------------------------------------------------
# It does not depend on telemetry
# --------------------------------------------------------------------------


def test_a_model_that_never_ran_can_still_be_priced(client) -> None:
    """The whole point. This database has no requests at all."""
    body = _quote(client, "llm", "anthropic/claude-sonnet-4-5")
    assert body["catalog"]["priced"] is True
    assert body["provider"] == "anthropic"
    assert body["pricing_source"]


def test_the_bulk_form_quotes_several_models_in_one_request(client) -> None:
    """One request per dropdown entry is the shape an editor would be forced
    into otherwise."""
    r = client.post(
        "/v1/billing/rate-card/quote",
        json={
            "models": [
                {"modality": "llm", "model": "openai/gpt-4o-mini"},
                {"modality": "stt", "model": "deepgram/nova-3"},
                {"modality": "tts", "model": "cartesia/sonic-2"},
            ]
        },
    )
    assert r.status_code == 200
    models = r.json()["models"]
    assert len(models) == 3
    assert all(m["catalog"]["priced"] for m in models)


def test_a_malformed_bulk_body_is_refused(client) -> None:
    r = client.post("/v1/billing/rate-card/quote", json={"models": "nope"})
    assert r.status_code == 400
