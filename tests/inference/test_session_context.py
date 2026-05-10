"""Tests for voicegateway.inference._session_context."""

from __future__ import annotations

import asyncio
import contextvars
import re

import pytest

from voicegateway.inference._session_context import (
    get_or_create_session_id,
    get_session_id,
    reset_session_id,
    start_session,
)

_VG_ID = re.compile(r"^vg-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.fixture(autouse=True)
def _isolate():
    # Ensure each test starts in a fresh ContextVar state. Run the test
    # body inside a copied context so writes don't leak across tests.
    ctx = contextvars.copy_context()
    yield ctx


class TestGetOrCreate:
    def test_first_call_returns_new_id(self):
        ctx = contextvars.copy_context()
        sid = ctx.run(get_or_create_session_id)
        assert _VG_ID.match(sid), f"expected vg-<uuid4>, got {sid!r}"

    def test_second_call_in_same_context_returns_same_id(self):
        ctx = contextvars.copy_context()
        first = ctx.run(get_or_create_session_id)
        second = ctx.run(get_or_create_session_id)
        assert first == second

    def test_new_context_returns_new_id(self):
        a = contextvars.copy_context().run(get_or_create_session_id)
        b = contextvars.copy_context().run(get_or_create_session_id)
        assert a != b


class TestGetWithoutCreating:
    def test_get_returns_none_when_no_session(self):
        ctx = contextvars.copy_context()
        assert ctx.run(get_session_id) is None

    def test_get_returns_existing_session(self):
        ctx = contextvars.copy_context()
        created = ctx.run(get_or_create_session_id)
        observed = ctx.run(get_session_id)
        assert observed == created


class TestReset:
    def test_reset_clears_session(self):
        def _scenario():
            sid = get_or_create_session_id()
            assert sid is not None
            reset_session_id()
            assert get_session_id() is None
            new_sid = get_or_create_session_id()
            assert new_sid != sid
            return sid, new_sid

        old, new = contextvars.copy_context().run(_scenario)
        assert old != new


class TestStartSession:
    """``start_session`` rolls a fresh id for sequential conversations
    in a single asyncio task — fixes the v0.0.5 review P1 #3 case
    where a worker reusing tasks would merge two sessions into one.
    """

    def test_start_session_returns_new_id(self):
        ctx = contextvars.copy_context()
        sid = ctx.run(start_session)
        assert _VG_ID.match(sid), f"expected vg-<uuid4>, got {sid!r}"

    def test_start_session_replaces_existing(self):
        def _scenario():
            first = get_or_create_session_id()
            second = start_session()
            after = get_session_id()
            return first, second, after

        first, second, after = contextvars.copy_context().run(_scenario)
        assert first != second
        assert after == second

    def test_simulated_worker_pattern(self):
        """One asyncio task handles two conversations sequentially.
        Without start_session() between them, the second conversation
        inherits the first's id (the bug). With start_session(), each
        conversation gets a distinct id.
        """

        def _scenario():
            sid_a = get_or_create_session_id()
            # Conversation B starts; the worker calls start_session
            # to roll a fresh id explicitly.
            sid_b = start_session()
            return sid_a, sid_b

        a, b = contextvars.copy_context().run(_scenario)
        assert a != b
        assert a.startswith("vg-")
        assert b.startswith("vg-")


class TestAsyncPropagation:
    async def test_session_propagates_to_awaited_coroutines(self):
        # Within one Task, awaited coroutines share the parent's context
        # by default. The session created at the top is visible inside
        # the inner coro.
        async def inner():
            return get_session_id()

        sid = get_or_create_session_id()
        observed = await inner()
        assert observed == sid

    async def test_independent_tasks_get_independent_ids(self):
        # asyncio.create_task copies the parent context AT TASK CREATION
        # time. If we create a session AFTER spawning the task, the task
        # sees None and creates its own ID. This documents the
        # documented v0.0.5 limitation: separate tasks may not share IDs.
        async def child():
            await asyncio.sleep(0)
            return get_or_create_session_id()

        # Task created BEFORE the parent sets a session: the task gets
        # its own snapshot.
        task = asyncio.create_task(child())
        # Now the parent creates its own session, AFTER the task already
        # captured an empty context.
        parent_sid = get_or_create_session_id()
        child_sid = await task
        assert parent_sid != child_sid
