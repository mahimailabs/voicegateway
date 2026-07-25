"""Reading back a LiveKit dispatch name, and keeping probe rows out of rollups.

Two halves of the same feature. The dashboard's play button can only target an
agent whose ``Job.agent_name`` VoiceGateway has actually OBSERVED (it is set at
worker registration inside the agent's own process, so it can never be invented
here), and the probe it places must not move the numbers on the card that
triggered it. Both go through real SQLite because both hinge on SQL details: a
two-scope predicate that must stay unambiguous, and a ``NOT LIKE`` that silently
drops NULL-metadata rows unless an ``IS NULL`` arm is present.
"""

from __future__ import annotations

import time
import uuid

from voicegateway.models.request_model import RequestRecord
from voicegateway.repository import agent_observations_repository as agent_obs
from voicegateway.repository import request_log_repository as reqlog
from voicegateway.services.storage_service import StorageService

# roll_up windows on wall clock, so rollup seeds have to land inside it.
_NOW = time.time() - 60.0


async def _store(tmp_path, name: str = "dispatch.db") -> StorageService:
    s = StorageService(str(tmp_path / name))
    await s._ensure_initialized()
    return s


async def _seed(
    storage: StorageService,
    *,
    agent_id: str | None,
    ts: float,
    metadata: dict | None = None,
    modality: str = "llm",
    cost: float = 0.0,
    status: str = "success",
    ttfb: float | None = None,
) -> None:
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=ts,
            modality=modality,
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="acme",
            cost_usd=cost,
            status=status,
            ttfb_ms=ttfb,
            agent_id=agent_id,
            metadata=metadata or {},
        )
    )


async def _dispatch_names(storage: StorageService, *args, **kw) -> dict[str, str]:
    async with storage._conn.session() as db:
        return await reqlog.read_last_seen_dispatch_name(db, *args, **kw)


# ---------------------------------------------------------------------------
# read_last_seen_dispatch_name: the three observable states
# ---------------------------------------------------------------------------


async def test_named_worker_reports_its_dispatch_name(tmp_path) -> None:
    storage = await _store(tmp_path)
    await _seed(
        storage, agent_id="support", ts=100.0, metadata={"dispatch_name": "support-bot"}
    )
    assert await _dispatch_names(storage) == {"support": "support-bot"}


async def test_automatic_dispatch_reports_empty_string_not_absence(tmp_path) -> None:
    """An empty name is an answer, not a missing value.

    LiveKit reports "" for a worker that registered without an agent_name, which
    means automatic dispatch. The dashboard probes such a worker by creating the
    room and issuing no dispatch, so this has to survive as a distinct state
    rather than collapsing into "never observed".
    """
    storage = await _store(tmp_path)
    await _seed(storage, agent_id="auto", ts=100.0, metadata={"dispatch_name": ""})
    names = await _dispatch_names(storage)
    assert names == {"auto": ""}
    assert "auto" in names  # the distinction the play button depends on


async def test_agent_with_no_observed_job_is_absent(tmp_path) -> None:
    storage = await _store(tmp_path)
    await _seed(storage, agent_id="quiet", ts=100.0, metadata={"room": "r1"})
    assert await _dispatch_names(storage) == {}


async def test_newest_row_wins_per_agent(tmp_path) -> None:
    storage = await _store(tmp_path)
    await _seed(storage, agent_id="a", ts=100.0, metadata={"dispatch_name": "old"})
    await _seed(storage, agent_id="a", ts=200.0, metadata={"dispatch_name": "new"})
    assert await _dispatch_names(storage) == {"a": "new"}


async def test_same_timestamp_row_without_a_name_does_not_win(tmp_path) -> None:
    """One turn writes several rows at once; only some carry the name.

    The JOIN matches on MAX(timestamp), so a sibling row stamped at the same
    instant is also returned. It must not be able to answer for the agent.
    """
    storage = await _store(tmp_path)
    await _seed(
        storage,
        agent_id="a",
        ts=300.0,
        modality="eou",
        metadata={"room": "r1", "eou": {"end_of_utterance_delay": 0.2}},
    )
    await _seed(storage, agent_id="a", ts=300.0, metadata={"dispatch_name": "picked"})
    assert await _dispatch_names(storage) == {"a": "picked"}


async def test_filters_to_requested_agents(tmp_path) -> None:
    storage = await _store(tmp_path)
    await _seed(storage, agent_id="a", ts=100.0, metadata={"dispatch_name": "an"})
    await _seed(storage, agent_id="b", ts=100.0, metadata={"dispatch_name": "bn"})
    assert await _dispatch_names(storage, ["b"]) == {"b": "bn"}


async def test_since_bounds_the_scan(tmp_path) -> None:
    storage = await _store(tmp_path)
    await _seed(storage, agent_id="stale", ts=100.0, metadata={"dispatch_name": "x"})
    assert await _dispatch_names(storage, since=150.0) == {}


async def test_unattributed_rows_are_ignored(tmp_path) -> None:
    storage = await _store(tmp_path)
    await _seed(storage, agent_id=None, ts=100.0, metadata={"dispatch_name": "orphan"})
    assert await _dispatch_names(storage) == {}


async def test_non_string_dispatch_name_is_rejected(tmp_path) -> None:
    """The LIKE only pre-filters JSON text; the parsed check is what decides."""
    storage = await _store(tmp_path)
    await _seed(storage, agent_id="weird", ts=100.0, metadata={"dispatch_name": 7})
    assert await _dispatch_names(storage) == {}


# ---------------------------------------------------------------------------
# Probe rows stay out of the agent's own numbers
# ---------------------------------------------------------------------------


async def test_probe_room_rows_are_excluded_from_rollup(tmp_path) -> None:
    """Pressing play must not move the card it was pressed on."""
    storage = await _store(tmp_path, "rollup.db")
    await _seed(storage, agent_id="a", ts=_NOW, cost=0.10, metadata={"room": "call-1"})
    await _seed(
        storage,
        agent_id="a",
        ts=_NOW + 1,
        cost=0.99,
        metadata={"room": f"{reqlog.PROBE_ROOM_PREFIX}a-deadbeef"},
    )
    async with storage._conn.session() as db:
        await agent_obs.roll_up(db)
        rows = await agent_obs.read_agents(db)
    row = next(r for r in rows if r.agent_id == "a")
    assert row.request_count == 1
    assert row.total_cost_usd == 0.10


async def test_rows_without_metadata_survive_the_probe_filter(tmp_path) -> None:
    """``NULL NOT LIKE x`` is NULL, so a naive filter would drop real traffic."""
    storage = await _store(tmp_path, "nullmeta.db")
    await _seed(storage, agent_id="a", ts=_NOW, cost=0.25, metadata=None)
    async with storage._conn.session() as db:
        await agent_obs.roll_up(db)
        rows = await agent_obs.read_agents(db)
    row = next(r for r in rows if r.agent_id == "a")
    assert row.request_count == 1
    assert row.total_cost_usd == 0.25


async def test_probe_rows_are_excluded_from_the_latency_waterfall(tmp_path) -> None:
    storage = await _store(tmp_path, "ttfb.db")
    await _seed(storage, agent_id="a", ts=_NOW, ttfb=200.0, metadata={"room": "call-1"})
    await _seed(
        storage,
        agent_id="a",
        ts=_NOW + 1,
        ttfb=9000.0,
        metadata={"room": f"{reqlog.PROBE_ROOM_PREFIX}a-deadbeef"},
    )
    async with storage._conn.session() as db:
        out = await reqlog.read_avg_ttfb_by_modality(db)
    assert out == {"a": {"llm": 200.0}}


async def test_a_room_merely_containing_the_prefix_is_still_real_traffic(
    tmp_path,
) -> None:
    """The exclusion is a prefix rule, so it must anchor at the start."""
    storage = await _store(tmp_path, "prefix.db")
    await _seed(
        storage,
        agent_id="a",
        ts=_NOW,
        cost=0.40,
        metadata={"room": f"customer-{reqlog.PROBE_ROOM_PREFIX}call"},
    )
    async with storage._conn.session() as db:
        await agent_obs.roll_up(db)
        rows = await agent_obs.read_agents(db)
    row = next(r for r in rows if r.agent_id == "a")
    assert row.request_count == 1
