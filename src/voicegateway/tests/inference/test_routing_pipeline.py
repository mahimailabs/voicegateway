"""End-to-end routing pipeline: three synthetic sessions per project."""

from __future__ import annotations

import time

import pytest

from voicegateway.core.config import ProjectConfig, RoutingConfig
from voicegateway.inference.session.context import (
    reset_routing_decision,
    set_routing_decision,
)
from voicegateway.models.request_model import RequestRecord
from voicegateway.services import routing_service as router
from voicegateway.services.storage_service import StorageService


@pytest.fixture
async def storage(tmp_path):
    yield StorageService(db_path=str(tmp_path / "pipeline.db"))
    reset_routing_decision()


def _req(session_id: str, project: str) -> RequestRecord:
    return RequestRecord(
        id=f"r-{session_id}-{time.time_ns()}",
        timestamp=time.time(),
        project=project,
        modality="stt",
        model_id="deepgram/test",
        provider="deepgram",
        input_units=0,
        output_units=0,
        cost_usd=0.05,
        pricing_source="t",
        ttfb_ms=None,
        total_latency_ms=200.0,
        status="success",
        fallback_from=None,
        error_message=None,
        metadata=None,
        session_id=session_id,
    )


async def project(project_id: str, budget_ms: int) -> ProjectConfig:
    return ProjectConfig(
        id=project_id,
        name=project_id,
        routing=RoutingConfig(
            budget_ms=budget_ms,
            rosters={
                "stt": ["deepgram"],
                "llm": ["openai", "groq"],
                "tts": ["cartesia", "elevenlabs"],
            },
            fallback_to_fastest=True,
        ),
    )


async def test_three_projects_get_distinct_routes(storage) -> None:
    """A tight, mid, and loose budget all pick from the same rosters"""
    await storage._ensure_initialized()

    tight = await project("tight", budget_ms=300)  # 250+80+150=480 > 300
    mid = await project("mid", budget_ms=600)
    loose = await project("loose", budget_ms=2000)

    for project_id, pc, sid in [
        ("tight", tight, "s-tight"),
        ("mid", mid, "s-mid"),
        ("loose", loose, "s-loose"),
    ]:
        async with storage._conn.session() as db:
            triple = await router.route_session(
                db, project_id=project_id, project_config=pc
            )
        set_routing_decision(
            (
                triple.stt,
                triple.llm,
                triple.tts,
                pc.routing.budget_ms,
                triple.budget_overrun,
            )
        )
        await storage.log_request(_req(sid, project_id))
        reset_routing_decision()

    # All three sessions should have routing columns populated.
    sessions = await storage.list_sessions()
    by_id = {s["id"]: s for s in sessions}

    assert by_id["s-tight"]["budget_overrun"] is True
    assert by_id["s-tight"]["budget_ms"] == 300

    assert by_id["s-mid"]["budget_overrun"] is False
    assert by_id["s-mid"]["budget_ms"] == 600
    assert by_id["s-mid"]["routed_llm"] == "groq"
    assert by_id["s-mid"]["routed_tts"] == "cartesia"

    assert by_id["s-loose"]["budget_overrun"] is False
    assert by_id["s-loose"]["budget_ms"] == 2000


async def test_pipeline_persists_immutable_triple(storage) -> None:
    """A second request on the same session with no routing decision"""
    await storage._ensure_initialized()
    pc = await project("acme", budget_ms=600)
    async with storage._conn.session() as db:
        triple = await router.route_session(db, project_id="acme", project_config=pc)
    set_routing_decision(
        (triple.stt, triple.llm, triple.tts, 600, triple.budget_overrun)
    )
    await storage.log_request(_req("s-acme", "acme"))

    reset_routing_decision()
    await storage.log_request(_req("s-acme", "acme"))

    detail = await storage.get_session("s-acme")
    assert detail is not None
    assert detail["routed_llm"] == triple.llm
    assert detail["budget_ms"] == 600
