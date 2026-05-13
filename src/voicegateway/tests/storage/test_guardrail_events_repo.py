"""Tests for guardrail audit event storage helpers."""

from __future__ import annotations

import time
import uuid

import pytest

from voicegateway.models.request import RequestRecord
from voicegateway.repository import guardrail_events
from voicegateway.storage.sqlite import SQLiteStorage


def _record(session_id: str, *, project: str = "default") -> RequestRecord:
    return RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=time.time(),
        modality="llm",
        model_id="openai/gpt-test",
        provider="openai",
        project=project,
        input_units=1.0,
        output_units=1.0,
        cost_usd=0.001,
        session_id=session_id,
    )


async def _db(storage: SQLiteStorage):
    return await storage._ensure_initialized()


async def test_create_and_list_events_by_session(tmp_path) -> None:
    storage = SQLiteStorage(str(tmp_path / "events.db"))
    await storage.log_request(_record("vg-a", project="support"))
    db = await _db(storage)
    try:
        event_id = await guardrail_events.create_event(
            db,
            session_id="vg-a",
            tenant_id="tenant-1",
            event_type="fired",
            category="pii",
            action="redact",
            context_excerpt="phone number",
        )
        await db.commit()
        rows = await guardrail_events.list_events_by_session(db, "vg-a")
    finally:
        await db.close()

    assert event_id > 0
    assert len(rows) == 1
    assert rows[0].project == "support"
    assert rows[0].tenant_id == "tenant-1"
    assert rows[0].category == "pii"
    assert rows[0].action == "redact"


async def test_bypass_rows_clear_category_and_are_excluded_from_aggregate(
    tmp_path,
) -> None:
    storage = SQLiteStorage(str(tmp_path / "events.db"))
    await storage.log_request(_record("vg-a"))
    db = await _db(storage)
    try:
        await guardrail_events.create_event(
            db,
            session_id="vg-a",
            event_type="bypassed",
            category="pii",
            action="block",
            context_excerpt="opt out",
        )
        await guardrail_events.create_event(
            db,
            session_id="vg-a",
            event_type="fired",
            category="prompt_injection",
            action="block",
            context_excerpt="ignore instructions",
        )
        await db.commit()
        rows = await guardrail_events.list_events(db, event_type="bypassed")
        counts = await guardrail_events.aggregate_counts(db)
    finally:
        await db.close()

    assert rows[0].category is None
    assert rows[0].action is None
    assert counts == [
        guardrail_events.GuardrailAggregate(
            category="prompt_injection",
            action="block",
            count=1,
        )
    ]


async def test_filters_and_top_sessions(tmp_path) -> None:
    storage = SQLiteStorage(str(tmp_path / "events.db"))
    await storage.log_request(_record("vg-a", project="support"))
    await storage.log_request(_record("vg-b", project="support"))
    await storage.log_request(_record("vg-c", project="other"))
    db = await _db(storage)
    try:
        for sid, tenant in (("vg-a", "t1"), ("vg-a", "t1"), ("vg-b", "t2")):
            await guardrail_events.create_event(
                db,
                session_id=sid,
                tenant_id=tenant,
                event_type="fired",
                category="financial",
                action="alert",
                context_excerpt="account details",
            )
        await guardrail_events.create_event(
            db,
            session_id="vg-c",
            tenant_id="t1",
            event_type="fired",
            category="medical",
            action="block",
            context_excerpt="diagnosis",
        )
        await db.commit()

        tenant_rows = await guardrail_events.list_events(
            db, project="support", tenant="t1"
        )
        top = await guardrail_events.top_sessions_by_category(
            db, category="financial", project="support"
        )
    finally:
        await db.close()

    assert [row.session_id for row in tenant_rows] == ["vg-a", "vg-a"]
    assert [(row.session_id, row.count) for row in top] == [("vg-a", 2), ("vg-b", 1)]


async def test_rejects_invalid_fired_event(tmp_path) -> None:
    storage = SQLiteStorage(str(tmp_path / "events.db"))
    db = await _db(storage)
    try:
        with pytest.raises(ValueError, match="unknown guardrail category"):
            await guardrail_events.create_event(
                db,
                session_id="vg-a",
                event_type="fired",
                category="bad",
                action="block",
            )
    finally:
        await db.close()
