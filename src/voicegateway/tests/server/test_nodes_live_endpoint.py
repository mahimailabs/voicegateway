"""GET /api/nodes/live: the newest node sample per target, with its freshness.

Four things these tests exist to hold:

1. **One entry per (node, source), and it is the newest one.** The same node is
   usually scraped twice per tick, once as ``livekit-server`` and once as
   ``node-exporter``, so the key is the pair. Collapsing to the node alone would
   drop half the series or silently prefer one exporter's view of the box.
2. **A NULL is data.** Every unmeasured column arrives as ``null``. The T3
   design stores NULL rather than 0 so the last hop can tell an unmeasured
   series from a measured zero, and a live view is where flattening it does the
   most harm: a node whose exporter went quiet would render as a node carrying
   no traffic.
3. **A stale target is not a healthy zero.** The newest sample for a dead target
   is still a sample, with real numbers and nothing in it that says they stopped
   being true. ``age_seconds`` and ``stale`` are what stop a crashed node
   rendering identically to a live one.
4. **Storage disabled is 503, not an empty fleet.** "Nothing is scraped" and
   "this deployment records nothing" are different facts.
"""

from __future__ import annotations

import time

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.repository import api_keys_repository as api_keys
from voicegateway.repository import node_samples_repository as node_samples
from voicegateway.server import build_app

_URL = "/api/nodes/live"

_BASE_CONFIG: dict = {
    "providers": {"openai": {"api_key": "k"}},
    "models": {"stt": {}, "llm": {}, "tts": {}},
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
}


def _write_config(tmp_path, extra: dict | None = None) -> str:
    path = tmp_path / "voicegw.yaml"
    with open(path, "w") as fh:
        yaml.dump({**_BASE_CONFIG, **(extra or {})}, fh)
    return str(path)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No ambient api key deciding whether auth applies."""
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "nodes-live.db"))
    return Gateway(config_path=_write_config(tmp_path))


@pytest.fixture
async def client(gateway):
    transport = ASGITransport(app=build_app(gateway))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _insert(
    storage,
    *,
    node: str,
    source: str,
    at_ms: int,
    outcome: str = "ok",
    **values,
) -> None:
    """Write one scrape row straight through the repository."""
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        await node_samples.insert_samples(
            db,
            [
                node_samples.NodeSampleInput(
                    node=node,
                    source=source,
                    at_ms=at_ms,
                    outcome=outcome,
                    values=values,
                )
            ],
        )


def _entry(body: dict, node: str, source: str) -> dict:
    matches = [e for e in body["nodes"] if e["node"] == node and e["source"] == source]
    assert len(matches) == 1, (
        f"expected exactly one entry for ({node}, {source}), got {len(matches)}"
    )
    return matches[0]


# --- one row per target, the newest ----------------------------------------


async def test_serves_only_the_newest_sample_per_target(client, gateway):
    """Three scrapes of one target collapse to the last one."""
    now = _now_ms()
    for offset, rooms in ((3000, 1), (2000, 2), (1000, 7)):
        await _insert(
            gateway.storage,
            node="sfu-1",
            source="livekit-server",
            at_ms=now - offset,
            rooms=rooms,
        )

    resp = await client.get(_URL)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["nodes"]) == 1
    assert _entry(body, "sfu-1", "livekit-server")["rooms"] == 7


async def test_one_node_scraped_by_two_exporters_is_two_entries(client, gateway):
    """``source`` is half the key. The same box reports different series to
    each exporter, and merging them would drop one set."""
    now = _now_ms()
    await _insert(
        gateway.storage,
        node="sfu-1",
        source="livekit-server",
        at_ms=now - 1000,
        rooms=4,
    )
    await _insert(
        gateway.storage,
        node="sfu-1",
        source="node-exporter",
        at_ms=now - 1000,
        load1=0.5,
    )

    body = (await client.get(_URL)).json()

    assert len(body["nodes"]) == 2
    assert _entry(body, "sfu-1", "livekit-server")["rooms"] == 4
    assert _entry(body, "sfu-1", "node-exporter")["load1"] == 0.5


async def test_every_target_in_the_fleet_appears(client, gateway):
    now = _now_ms()
    for node in ("sfu-1", "sfu-2", "sip-1"):
        await _insert(
            gateway.storage,
            node=node,
            source="livekit-server",
            at_ms=now - 500,
            rooms=1,
        )

    body = (await client.get(_URL)).json()

    assert sorted(e["node"] for e in body["nodes"]) == ["sfu-1", "sfu-2", "sip-1"]


# --- a NULL is data --------------------------------------------------------


async def test_an_unmeasured_column_arrives_as_null_never_zero(client, gateway):
    """The distinction the whole T3 design exists to preserve.

    ``rooms`` is measured at 0 here and ``participants`` was never scraped. If
    both came back as 0 a reader could not tell an idle node from an unwatched
    one, and the unwatched one is the emergency.
    """
    await _insert(
        gateway.storage,
        node="sfu-1",
        source="livekit-server",
        at_ms=_now_ms() - 1000,
        rooms=0,
    )

    entry = _entry((await client.get(_URL)).json(), "sfu-1", "livekit-server")

    assert entry["rooms"] == 0, "a measured zero must survive as zero"
    assert entry["participants"] is None, (
        "an unmeasured series was defaulted to 0, which reads as a node "
        "carrying no traffic rather than one nobody could see"
    )
    assert entry["packet_bytes_total"] is None


async def test_a_failed_scrape_keeps_its_outcome_and_null_values(client, gateway):
    """A row that failed is still served, because the failure is the news."""
    await _insert(
        gateway.storage,
        node="sfu-1",
        source="livekit-server",
        at_ms=_now_ms() - 1000,
        outcome="timeout",
    )

    entry = _entry((await client.get(_URL)).json(), "sfu-1", "livekit-server")

    assert entry["outcome"] == "timeout"
    assert entry["rooms"] is None


# --- freshness -------------------------------------------------------------


async def test_a_target_past_the_window_is_reported_stale(client, gateway):
    """The newest sample of a dead node still has real numbers in it.

    Nothing on the row itself says they stopped being true, so without ``stale``
    a crashed node renders exactly like a healthy one, and more convincingly the
    longer it stays down.
    """
    now = _now_ms()
    await _insert(
        gateway.storage,
        node="fresh",
        source="livekit-server",
        at_ms=now - 2_000,
        rooms=1,
    )
    await _insert(
        gateway.storage,
        node="dead",
        source="livekit-server",
        at_ms=now - 120_000,
        rooms=99,
    )

    body = (await client.get(_URL)).json()

    fresh = _entry(body, "fresh", "livekit-server")
    dead = _entry(body, "dead", "livekit-server")
    assert fresh["stale"] is False
    assert dead["stale"] is True, (
        "a target whose newest sample is two minutes old was served as current"
    )
    # The reading is still there; it is labelled, not withheld.
    assert dead["rooms"] == 99
    assert dead["age_seconds"] >= 100


async def test_the_threshold_travels_with_the_answer(client, gateway):
    """A verdict whose threshold is invisible cannot be checked by the reader."""
    await _insert(
        gateway.storage,
        node="sfu-1",
        source="livekit-server",
        at_ms=_now_ms() - 1000,
        rooms=1,
    )

    body = (await client.get(_URL)).json()

    assert body["stale_after_seconds"] == 15.0


async def test_the_caller_may_widen_the_window(client, gateway):
    """The same sample, judged against a looser bar, is not stale."""
    await _insert(
        gateway.storage,
        node="sfu-1",
        source="livekit-server",
        at_ms=_now_ms() - 30_000,
        rooms=1,
    )

    default = (await client.get(_URL)).json()
    widened = (await client.get(f"{_URL}?stale_after_seconds=60")).json()

    assert _entry(default, "sfu-1", "livekit-server")["stale"] is True
    assert _entry(widened, "sfu-1", "livekit-server")["stale"] is False
    assert widened["stale_after_seconds"] == 60.0


async def test_an_absurd_window_is_refused(client):
    """Past a few minutes the word "live" stops meaning anything."""
    resp = await client.get(f"{_URL}?stale_after_seconds=99999")
    assert resp.status_code == 422


# --- empty and disabled are different --------------------------------------


async def test_no_samples_is_an_empty_fleet_not_an_error(client):
    body = (await client.get(_URL)).json()
    assert body["nodes"] == []
    assert body["samples_stored"] == 0


async def test_samples_stored_separates_quiet_from_unconfigured(client, gateway):
    """With no scrape target the list is honestly empty, and only the row count
    says whether this deployment scrapes at all."""
    await _insert(
        gateway.storage,
        node="sfu-1",
        source="livekit-server",
        at_ms=_now_ms() - 1000,
        rooms=1,
    )

    body = (await client.get(_URL)).json()

    assert body["samples_stored"] == 1


async def test_storage_disabled_returns_503_not_an_empty_fleet(tmp_path, monkeypatch):
    """ "Nothing is scraped" and "this deployment records nothing" are
    different facts."""
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    config = _write_config(tmp_path, {"cost_tracking": {"enabled": False}})
    gw = Gateway(config_path=config)
    assert gw.storage is None
    transport = ASGITransport(app=build_app(gw, enable_dashboard=False))

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(_URL)

    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"]


# --- auth ------------------------------------------------------------------


async def test_the_operator_default_with_no_keys_still_reads_it(client, gateway):
    """No credential = the self-hosted operator, unchanged."""
    await _insert(
        gateway.storage,
        node="sfu-1",
        source="livekit-server",
        at_ms=_now_ms() - 1000,
        rooms=1,
    )

    resp = await client.get(_URL)

    assert resp.status_code == 200
    assert len(resp.json()["nodes"]) == 1


async def test_a_tenant_key_is_refused_rather_than_shown_the_fleet(client, gateway):
    """A node is infrastructure and serves every tenant at once, so there is no
    scoped answer. Serving the deployment-wide one would publish every other
    tenant's infrastructure."""
    await _insert(
        gateway.storage,
        node="sfu-1",
        source="livekit-server",
        at_ms=_now_ms() - 1000,
        rooms=1,
    )
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(
            db, name="acme-ui", tenant_id="acme", scopes="read,write,ingest,admin"
        )

    resp = await client.get(
        _URL, headers={"Authorization": f"Bearer {created.plaintext}"}
    )

    assert resp.status_code == 403


# --- the threshold is not invented here ------------------------------------


def test_the_default_window_matches_the_scrape_interval() -> None:
    """Restated on the read side, so a guard keeps the two equal.

    The endpoint must not decide on its own what "current" means. If the scrape
    worker's interval changes and this does not, every target starts reporting
    stale (or nothing ever does), and neither failure announces itself.
    """
    from voicegateway.middleware import node_samples_worker_middleware as worker
    from voicegateway.server.api.dashboard import nodes_live

    assert (
        nodes_live.DEFAULT_STALE_AFTER_SECONDS == worker._DEFAULT_POLL_INTERVAL_SECONDS
    )


async def test_two_samples_sharing_a_timestamp_resolve_stably(client, gateway):
    """``id`` breaks the tie, the same way ``list_samples`` breaks it.

    Without a tiebreak "the latest" is whichever row the backend happened to
    return first, which is not stable across backends or even across runs, and
    the live view would flicker between two readings of the same instant.
    """
    at_ms = _now_ms() - 1000
    await _insert(
        gateway.storage,
        node="sfu-1",
        source="livekit-server",
        at_ms=at_ms,
        rooms=1,
    )
    await _insert(
        gateway.storage,
        node="sfu-1",
        source="livekit-server",
        at_ms=at_ms,
        rooms=2,
    )

    first = _entry((await client.get(_URL)).json(), "sfu-1", "livekit-server")
    second = _entry((await client.get(_URL)).json(), "sfu-1", "livekit-server")

    assert first["rooms"] == 2, "the later-written row did not win the tie"
    assert second["rooms"] == first["rooms"], "the answer was not stable"
